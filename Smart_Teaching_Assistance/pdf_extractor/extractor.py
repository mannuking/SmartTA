"""
PDF Image Extractor module for extracting images, formulas, and tables from PDFs.
"""

import hashlib
import json
from pathlib import Path
from typing import List, Dict, Optional
import logging

import fitz  # PyMuPDF
from PIL import Image
# Swap google-generativeai for our MiniMax-M3 shim. Same surface.
from .. import minimax_client as genai

logger = logging.getLogger(__name__)


class PDFImageExtractor:
    """Extracts images and visual elements from PDF documents."""
    
    def __init__(self, gemini_api_key: str, db_path: str = "smart_teaching_assistant.db", db_connection=None, user_id=None):
        """
        Initialize PDF extractor.
        
        Args:
            gemini_api_key: API key for Gemini Vision
            db_path: Path to SQLite database
            db_connection: Optional existing database connection
            user_id: Current user ID for multi-tenancy
        """
        self.gemini_api_key = gemini_api_key
        self.db_path = db_path
        self.user_id = user_id
        
        # Configure Gemini API
        genai.configure(api_key=gemini_api_key)
        
        # Import database module
        if db_connection:
            self.db = db_connection
        else:
            from ..database import get_database
            self.db = get_database(db_path)
        
        logger.info("PDFImageExtractor initialized")
    
    def get_pdf_hash(self, pdf_content: bytes) -> str:
        """
        Generate MD5 hash for PDF content.
        
        Args:
            pdf_content: PDF file content as bytes
            
        Returns:
            MD5 hash string
        """
        return hashlib.md5(pdf_content).hexdigest()
    
    def is_pdf_processed(self, pdf_hash: str) -> bool:
        """
        Check if PDF has been previously processed.
        
        Args:
            pdf_hash: MD5 hash of PDF content
            
        Returns:
            True if PDF exists in database
        """
        result = self.db.fetchone(
            "SELECT COUNT(*) as count FROM pdf_images WHERE pdf_hash = ? AND user_id = ?",
            (pdf_hash, self.user_id)
        )
        return result["count"] > 0 if result else False
    
    def get_extracted_images(self, pdf_hash: str) -> List[Dict]:
        """
        Retrieve all cached images for a PDF.
        
        Args:
            pdf_hash: MD5 hash of PDF content
            
        Returns:
            List of image metadata dictionaries
        """
        rows = self.db.fetchall(
            """SELECT id, pdf_hash, filename, page_number, element_type, 
                      bbox, image_path, caption, description, concepts, created_at
               FROM pdf_images WHERE pdf_hash = ? AND user_id = ?""",
            (pdf_hash, self.user_id)
        )
        
        return [dict(row) for row in rows]
    
    def extract_images_from_pdf(
        self, 
        pdf_path: str, 
        pdf_content: bytes = None,
        output_dir: str = "extracted_images"
    ) -> List[Dict]:
        """
        Extract images from PDF using PyMuPDF.
        
        Args:
            pdf_path: Path to PDF file
            pdf_content: Optional PDF content as bytes
            output_dir: Directory to save extracted images
            
        Returns:
            List of image metadata dictionaries
        """
        # Read PDF content if not provided
        if pdf_content is None:
            with open(pdf_path, 'rb') as f:
                pdf_content = f.read()
        
        # Generate PDF hash
        pdf_hash = self.get_pdf_hash(pdf_content)
        
        # Check cache
        if self.is_pdf_processed(pdf_hash):
            logger.info(f"PDF already processed (hash: {pdf_hash}), returning cached results")
            return self.get_extracted_images(pdf_hash)
        
        # Extract images
        logger.info(f"Extracting images from PDF: {pdf_path}")
        extracted_images = []
        seen_image_hashes = set()
        
        # Create output directory
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        
        # Open PDF
        doc = fitz.open(pdf_path)
        filename = Path(pdf_path).name
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            image_list = page.get_images()
            
            for img_index, img in enumerate(image_list):
                try:
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    width = base_image["width"]
                    height = base_image["height"]
                    
                    # Filter 1: Ignore small images (icons, dividers, artifacts)
                    if width < 200 or height < 200:
                        continue
                        
                    # Filter 2: Ignore extreme aspect ratios (lines, banners)
                    aspect_ratio = width / height
                    if aspect_ratio > 5 or aspect_ratio < 0.2:
                        continue
                    
                    # Filter 3: Deduplicate repeated images (logos, headers)
                    img_hash = hashlib.md5(image_bytes).hexdigest()
                    if img_hash in seen_image_hashes:
                        continue
                    seen_image_hashes.add(img_hash)
                    
                    # Save image
                    image_filename = f"{pdf_hash}_page{page_num + 1}_img{img_index + 1}.{image_ext}"
                    image_path = Path(output_dir) / image_filename
                    
                    with open(image_path, "wb") as img_file:
                        img_file.write(image_bytes)
                    
                    # Get bounding box (approximate)
                    bbox = [0, 0, 0, 0]  # Placeholder, PyMuPDF doesn't easily provide exact bbox
                    
                    # Store metadata
                    metadata = {
                        "pdf_hash": pdf_hash,
                        "filename": filename,
                        "page_number": page_num + 1,
                        "element_type": "figure",
                        "bbox": json.dumps(bbox),
                        "image_path": str(image_path),
                        "caption": "",
                        "description": "",
                        "concepts": json.dumps([])
                    }
                    
                    extracted_images.append(metadata)
                    
                except Exception as e:
                    logger.error(f"Error extracting image {img_index} from page {page_num}: {e}")
        
        doc.close()
        
        # Store in database
        for img_meta in extracted_images:
            self.store_image_metadata(**img_meta)
        
        logger.info(f"Extracted {len(extracted_images)} unique images from PDF")
        return extracted_images

    def extract_text(self, pdf_path: str) -> str:
        """
        Extract text content from PDF.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Extracted text content
        """
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    
    def store_image_metadata(
        self,
        pdf_hash: str,
        filename: str,
        page_number: int,
        element_type: str,
        bbox: str,
        image_path: str,
        caption: str = "",
        description: str = "",
        concepts: str = "[]"
    ) -> None:
        """
        Store image metadata in database.
        
        Args:
            pdf_hash: MD5 hash of PDF
            filename: Original PDF filename
            page_number: Page number where image was found
            element_type: Type of element (figure, table, formula)
            bbox: Bounding box coordinates as JSON string
            image_path: Path to saved image file
            caption: Image caption
            description: Image description
            concepts: Key concepts as JSON array string
        """
        self.db.execute(
            """INSERT INTO pdf_images 
               (pdf_hash, user_id, filename, page_number, element_type, bbox, 
                image_path, caption, description, concepts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pdf_hash, self.user_id, filename, page_number, element_type, bbox,
             image_path, caption, description, concepts)
        )
    
    def generate_image_description(self, image_path: str, element_type: str = "figure") -> Dict:
            """
            Generate educational description using Gemini Vision.

            Args:
                image_path: Path to image file
                element_type: Type of element (figure, table, formula)

            Returns:
                Dictionary with caption, description, and concepts
            """
            # Fallback descriptions based on element type
            fallback_descriptions = {
                "figure": {
                    "caption": "Educational figure",
                    "description": "This figure contains visual educational content that illustrates key concepts.",
                    "concepts": ["visual representation"]
                },
                "table": {
                    "caption": "Educational table",
                    "description": "This table presents structured data or information in a tabular format.",
                    "concepts": ["data presentation", "structured information"]
                },
                "formula": {
                    "caption": "Mathematical formula",
                    "description": "This formula represents a mathematical or scientific relationship.",
                    "concepts": ["mathematical expression", "equation"]
                }
            }

            # Get fallback for this element type
            fallback = fallback_descriptions.get(
                element_type, 
                fallback_descriptions["figure"]
            )

            try:
                # Load image
                img = Image.open(image_path)

                # Create prompt
                prompt = f"""Analyze this educational {element_type} and provide:
    1. A brief caption (1 sentence)
    2. A detailed description explaining what it shows (2-3 sentences)
    3. Key concepts illustrated (comma-separated list)

    Format your response as:
    Caption: [caption]
    Description: [description]
    Concepts: [concept1, concept2, concept3]"""

                # Generate description using Gemini with retry logic
                max_retries = 2
                last_error = None

                for attempt in range(max_retries):
                    try:
                        model = genai.GenerativeModel('gemini-3-flash-preview')
                        response = model.generate_content([prompt, img])

                        # Check if response is valid
                        if not response or not hasattr(response, 'text'):
                            raise ValueError("Empty or invalid response from Gemini API")

                        # Parse response
                        text = response.text
                        caption = ""
                        description = ""
                        concepts = []

                        for line in text.split('\n'):
                            line = line.strip()
                            if line.startswith('Caption:'):
                                caption = line.replace('Caption:', '').strip()
                            elif line.startswith('Description:'):
                                description = line.replace('Description:', '').strip()
                            elif line.startswith('Concepts:'):
                                concepts_str = line.replace('Concepts:', '').strip()
                                concepts = [c.strip() for c in concepts_str.split(',') if c.strip()]

                        # Validate parsed content
                        if not caption or not description:
                            logger.warning(f"Incomplete response from Gemini API, using fallback")
                            return fallback.copy()

                        return {
                            "caption": caption,
                            "description": description,
                            "concepts": concepts if concepts else fallback["concepts"]
                        }

                    except Exception as e:
                        last_error = e
                        logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
                        if attempt < max_retries - 1:
                            # Wait before retry (exponential backoff)
                            import time
                            time.sleep(2 ** attempt)
                        continue

                # All retries failed
                logger.error(f"All retries failed for image description generation: {last_error}")
                return fallback.copy()

            except FileNotFoundError:
                logger.error(f"Image file not found: {image_path}")
                return fallback.copy()
            except Exception as e:
                logger.error(f"Error generating image description: {e}")
                return fallback.copy()


    
    def find_relevant_images(
        self, 
        content_text: str, 
        pdf_hash: str, 
        top_k: int = 3
    ) -> List[Dict]:
        """
        Find most relevant images using TF-IDF similarity.
        
        Args:
            content_text: Text content to match against
            pdf_hash: PDF hash to filter images
            top_k: Number of top images to return
            
        Returns:
            List of images with similarity scores
        """
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        
        # Get all images for this PDF
        images = self.get_extracted_images(pdf_hash)
        
        if not images:
            return []
        
        # Prepare texts for similarity calculation
        image_texts = []
        for img in images:
            # Combine caption, description, and concepts
            concepts = json.loads(img.get('concepts', '[]'))
            text = f"{img.get('caption', '')} {img.get('description', '')} {' '.join(concepts)}"
            image_texts.append(text)
        
        # Calculate TF-IDF similarity
        try:
            vectorizer = TfidfVectorizer()
            all_texts = [content_text] + image_texts
            tfidf_matrix = vectorizer.fit_transform(all_texts)
            
            # Calculate cosine similarity
            similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:]).flatten()
            
            # Add similarity scores to images
            for i, img in enumerate(images):
                img['similarity_score'] = float(similarities[i])
            
            # Sort by similarity and return top k
            images.sort(key=lambda x: x['similarity_score'], reverse=True)
            return images[:top_k]
            
        except Exception as e:
            logger.error(f"Error calculating image similarity: {e}")
            return images[:top_k]
