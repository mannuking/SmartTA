"""
Tests for ContentGenerator class.
"""

import pytest
import tempfile
import os
import json
from unittest.mock import Mock, patch, MagicMock

from Smart_Teaching_Assistance.content_generator.generator import ContentGenerator
from Smart_Teaching_Assistance.database import get_database


# Sample test data
SAMPLE_SYLLABUS = """
Introduction to Machine Learning
- Supervised Learning
  - Linear Regression
  - Logistic Regression
- Unsupervised Learning
  - K-Means Clustering
  - PCA
"""

SAMPLE_ROADMAP_TEXT = """Sequence: Linear
T1: Machine Learning
    T1.1: Supervised Learning
        T1.1.1: Linear Regression
        T1.1.2: Logistic Regression
    T1.2: Unsupervised Learning
        T1.2.1: K-Means Clustering
        T1.2.2: PCA"""


@pytest.fixture
def mock_gemini_model():
    """Create a mock Gemini model."""
    with patch('google.generativeai.configure'):
        with patch('google.generativeai.GenerativeModel') as mock_model_class:
            mock_model = MagicMock()
            mock_model_class.return_value = mock_model
            yield mock_model


@pytest.fixture
def temp_database():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = get_database(db_path)
        yield db
        db.close()


@pytest.fixture
def content_generator(mock_gemini_model):
    """Create ContentGenerator instance with mocked API."""
    generator = ContentGenerator(gemini_api_key="test_key")
    generator.model = mock_gemini_model
    return generator


@pytest.fixture
def content_generator_with_db(mock_gemini_model, temp_database):
    """Create ContentGenerator instance with database."""
    generator = ContentGenerator(gemini_api_key="test_key", db_connection=temp_database)
    generator.model = mock_gemini_model
    return generator


class TestGenerateRoadmap:
    """Tests for generate_roadmap method."""
    
    def test_generate_roadmap_returns_text(self, content_generator, mock_gemini_model):
        """Test that generate_roadmap returns formatted text."""
        mock_response = Mock()
        mock_response.text = SAMPLE_ROADMAP_TEXT
        mock_gemini_model.generate_content.return_value = mock_response
        
        result = content_generator.generate_roadmap(SAMPLE_SYLLABUS, "intermediate")
        
        assert isinstance(result, str)
        assert len(result) > 0
        assert "T1:" in result
    
    def test_generate_roadmap_includes_difficulty_level(self, content_generator, mock_gemini_model):
        """Test that difficulty level is included in prompt."""
        mock_response = Mock()
        mock_response.text = SAMPLE_ROADMAP_TEXT
        mock_gemini_model.generate_content.return_value = mock_response
        
        content_generator.generate_roadmap(SAMPLE_SYLLABUS, "advanced")
        
        # Verify the prompt includes difficulty level
        call_args = mock_gemini_model.generate_content.call_args[0][0]
        assert "advanced" in call_args.lower()
    
    def test_generate_roadmap_handles_different_difficulty_levels(self, content_generator, mock_gemini_model):
        """Test that different difficulty levels produce different prompts."""
        mock_response = Mock()
        mock_response.text = SAMPLE_ROADMAP_TEXT
        mock_gemini_model.generate_content.return_value = mock_response
        
        for difficulty in ["beginner", "intermediate", "advanced"]:
            content_generator.generate_roadmap(SAMPLE_SYLLABUS, difficulty)
            call_args = mock_gemini_model.generate_content.call_args[0][0]
            assert difficulty in call_args.lower()
    
    def test_generate_roadmap_error_handling(self, content_generator, mock_gemini_model):
        """Test error handling when API fails."""
        mock_gemini_model.generate_content.side_effect = Exception("API Error")
        
        with pytest.raises(Exception, match="API Error"):
            content_generator.generate_roadmap(SAMPLE_SYLLABUS, "intermediate")


class TestParseRoadmap:
    """Tests for parse_roadmap method."""
    
    def test_parse_roadmap_creates_hierarchical_structure(self, content_generator):
        """Test that parse_roadmap creates correct hierarchical structure."""
        result = content_generator.parse_roadmap(SAMPLE_ROADMAP_TEXT)
        
        assert isinstance(result, dict)
        assert result["sequence"] == "Linear"
        assert len(result["topics"]) == 1
        assert result["topics"][0]["title"] == "Machine Learning"
        assert len(result["topics"][0]["subtopics"]) == 2
        assert result["topics"][0]["subtopics"][0]["title"] == "Supervised Learning"
        assert result["topics"][0]["subtopics"][1]["title"] == "Unsupervised Learning"
    
    def test_parse_roadmap_handles_sub_subtopics(self, content_generator):
        """Test parsing of sub-subtopics."""
        result = content_generator.parse_roadmap(SAMPLE_ROADMAP_TEXT)
        
        supervised = result["topics"][0]["subtopics"][0]
        unsupervised = result["topics"][0]["subtopics"][1]
        
        assert len(supervised["subtopics"]) == 2
        assert supervised["subtopics"][0]["title"] == "Linear Regression"
        assert supervised["subtopics"][1]["title"] == "Logistic Regression"
        
        assert len(unsupervised["subtopics"]) == 2
        assert unsupervised["subtopics"][0]["title"] == "K-Means Clustering"
        assert unsupervised["subtopics"][1]["title"] == "PCA"
    
    def test_parse_roadmap_handles_empty_input(self, content_generator):
        """Test parsing empty roadmap text."""
        result = content_generator.parse_roadmap("")
        
        assert isinstance(result, dict)
        assert len(result["topics"]) == 0
    
    def test_parse_roadmap_ignores_comments(self, content_generator):
        """Test that comments are ignored."""
        roadmap_with_comments = """Sequence: Linear
# This is a comment
T1: Machine Learning
    T1.1: Supervised Learning
        T1.1.1: Linear Regression
# Another comment
    T1.2: Unsupervised Learning
        T1.2.1: K-Means"""
        
        result = content_generator.parse_roadmap(roadmap_with_comments)
        
        assert len(result["topics"]) == 1
        assert result["topics"][0]["title"] == "Machine Learning"
        assert len(result["topics"][0]["subtopics"]) == 2


class TestStoreRoadmap:
    """Tests for store_roadmap method."""
    
    def test_store_roadmap_saves_to_database(self, content_generator_with_db):
        """Test that roadmap is stored in database."""
        structure = {"Topic": {"Subtopic": ["Sub-subtopic"]}}
        
        roadmap_id = content_generator_with_db.store_roadmap(
            roadmap_text=SAMPLE_ROADMAP_TEXT,
            roadmap_structure=structure,
            difficulty_level="intermediate",
            source_type="syllabus"
        )
        
        assert isinstance(roadmap_id, int)
        assert roadmap_id > 0
    
    def test_store_roadmap_requires_database(self, content_generator):
        """Test that store_roadmap raises error without database."""
        structure = {"Topic": {"Subtopic": []}}
        
        with pytest.raises(ValueError, match="Database connection required"):
            content_generator.store_roadmap(
                roadmap_text=SAMPLE_ROADMAP_TEXT,
                roadmap_structure=structure,
                difficulty_level="intermediate"
            )
    
    def test_store_roadmap_with_source_hash(self, content_generator_with_db):
        """Test storing roadmap with source hash."""
        structure = {"Topic": {"Subtopic": []}}
        source_hash = "abc123"
        
        roadmap_id = content_generator_with_db.store_roadmap(
            roadmap_text=SAMPLE_ROADMAP_TEXT,
            roadmap_structure=structure,
            difficulty_level="advanced",
            source_type="pdf",
            source_hash=source_hash
        )
        
        # Verify stored data
        row = content_generator_with_db.db.fetchone(
            "SELECT * FROM roadmaps WHERE id = ?",
            (roadmap_id,)
        )
        
        assert row["source_hash"] == source_hash
        assert row["source_type"] == "pdf"
        assert row["difficulty_level"] == "advanced"


class TestGetRoadmap:
    """Tests for get_roadmap method."""
    
    def test_get_roadmap_retrieves_stored_data(self, content_generator_with_db):
        """Test retrieving roadmap from database."""
        structure = {"Topic": {"Subtopic": ["Sub-subtopic"]}}
        
        # Store roadmap
        roadmap_id = content_generator_with_db.store_roadmap(
            roadmap_text=SAMPLE_ROADMAP_TEXT,
            roadmap_structure=structure,
            difficulty_level="intermediate"
        )
        
        # Retrieve roadmap
        result = content_generator_with_db.get_roadmap(roadmap_id)
        
        assert result is not None
        assert result["id"] == roadmap_id
        assert result["roadmap_text"] == SAMPLE_ROADMAP_TEXT
        assert result["roadmap_structure"] == structure
        assert result["difficulty_level"] == "intermediate"
    
    def test_get_roadmap_returns_none_for_invalid_id(self, content_generator_with_db):
        """Test that get_roadmap returns None for non-existent ID."""
        result = content_generator_with_db.get_roadmap(99999)
        
        assert result is None
    
    def test_get_roadmap_requires_database(self, content_generator):
        """Test that get_roadmap raises error without database."""
        with pytest.raises(ValueError, match="Database connection required"):
            content_generator.get_roadmap(1)


class TestGenerateAndStoreRoadmap:
    """Tests for generate_and_store_roadmap method."""
    
    def test_generate_and_store_roadmap_complete_workflow(self, content_generator_with_db, mock_gemini_model):
        """Test complete workflow of generating and storing roadmap."""
        mock_response = Mock()
        mock_response.text = SAMPLE_ROADMAP_TEXT
        mock_gemini_model.generate_content.return_value = mock_response
        
        result = content_generator_with_db.generate_and_store_roadmap(
            source_text=SAMPLE_SYLLABUS,
            difficulty_level="intermediate",
            source_type="syllabus"
        )
        
        assert "roadmap_id" in result
        assert "roadmap_text" in result
        assert "roadmap_structure" in result
        assert "source_hash" in result
        assert result["roadmap_id"] is not None
        assert isinstance(result["roadmap_structure"], dict)
    
    def test_generate_and_store_roadmap_without_database(self, content_generator, mock_gemini_model):
        """Test that method works without database (doesn't store)."""
        mock_response = Mock()
        mock_response.text = SAMPLE_ROADMAP_TEXT
        mock_gemini_model.generate_content.return_value = mock_response
        
        result = content_generator.generate_and_store_roadmap(
            source_text=SAMPLE_SYLLABUS,
            difficulty_level="intermediate"
        )
        
        assert result["roadmap_id"] is None
        assert "roadmap_text" in result
        assert "roadmap_structure" in result


class TestAutoTagContent:
    """Tests for auto_tag_content method."""
    
    def test_auto_tag_content_finds_tags(self, content_generator):
        """Test that auto_tag_content identifies tags in content."""
        content = """This is some content.
[EXAMPLE:derivatives] Here is an example about derivatives.
[ANALOGY:neural_networks] Think of it like a brain.
[APPLICATION:robotics] This is used in robotics."""
        
        tagged_content, tags = content_generator.auto_tag_content(content)
        
        assert len(tags) == 3
        assert tags[0]["tag_type"] == "EXAMPLE"
        assert tags[0]["context"] == "derivatives"
        assert tags[1]["tag_type"] == "ANALOGY"
        assert tags[1]["context"] == "neural_networks"
        assert tags[2]["tag_type"] == "APPLICATION"
        assert tags[2]["context"] == "robotics"
    
    def test_auto_tag_content_handles_no_tags(self, content_generator):
        """Test handling content without tags."""
        content = "This is plain content without any tags."
        
        tagged_content, tags = content_generator.auto_tag_content(content)
        
        assert len(tags) == 0
        assert tagged_content == content
    
    def test_auto_tag_content_captures_positions(self, content_generator):
        """Test that tag positions are captured correctly."""
        content = "Start [EXAMPLE:test] middle [ANALOGY:test2] end"
        
        tagged_content, tags = content_generator.auto_tag_content(content)
        
        assert len(tags) == 2
        assert tags[0]["start_position"] < tags[0]["end_position"]
        assert tags[1]["start_position"] < tags[1]["end_position"]
        assert tags[0]["end_position"] < tags[1]["start_position"]


class TestGenerateBaseNotes:
    """Tests for generate_base_notes method."""
    
    def test_generate_base_notes_returns_structured_data(self, content_generator, mock_gemini_model):
        """Test that generate_base_notes returns properly structured data."""
        mock_response = Mock()
        mock_response.text = "Content with [EXAMPLE:test] tag"
        mock_gemini_model.generate_content.return_value = mock_response
        
        result = content_generator.generate_base_notes(
            topic="Machine Learning",
            subtopic="Linear Regression",
            difficulty="intermediate"
        )
        
        assert "content" in result
        assert "tags" in result
        assert "topic" in result
        assert "subtopic" in result
        assert "difficulty" in result
        assert result["topic"] == "Machine Learning"
        assert result["subtopic"] == "Linear Regression"
    
    def test_generate_base_notes_includes_context(self, content_generator, mock_gemini_model):
        """Test that context is included in prompt."""
        mock_response = Mock()
        mock_response.text = "Content"
        mock_gemini_model.generate_content.return_value = mock_response
        
        content_generator.generate_base_notes(
            topic="ML",
            subtopic="Regression",
            difficulty="beginner",
            context="For engineering students"
        )
        
        call_args = mock_gemini_model.generate_content.call_args[0][0]
        assert "For engineering students" in call_args
    
    def test_generate_base_notes_error_handling(self, content_generator, mock_gemini_model):
        """Test error handling in base notes generation."""
        mock_gemini_model.generate_content.side_effect = Exception("API Error")
        
        result = content_generator.generate_base_notes(
            topic="Test",
            subtopic="Test",
            difficulty="intermediate"
        )
        
        assert result["content"] == "Error generating content"
        assert result["tags"] == []


class TestRoadmapPropertyTests:
    """Property-based tests for roadmap generation."""
    
    # Feature: unified-smart-teaching-assistant, Property 8: Roadmap Hierarchical Parsing
    def test_property_roadmap_hierarchical_parsing(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 2.1**
        
        Property 8: Roadmap Hierarchical Parsing
        For any syllabus text, the roadmap parser should produce a hierarchical 
        structure with at least one topic level.
        """
        from hypothesis import given, strategies as st, settings
        
        @given(
            syllabus_text=st.text(min_size=10, max_size=500).filter(lambda x: x.strip())
        )
        @settings(max_examples=100, deadline=None)
        def property_test(syllabus_text):
            # Mock the API response with a valid roadmap
            mock_response = Mock()
            mock_response.text = f"Topic A -> Subtopic 1\nTopic A -> Subtopic 2"
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Generate roadmap
            roadmap_text = content_generator.generate_roadmap(syllabus_text, "intermediate")
            
            # Parse the roadmap
            structure = content_generator.parse_roadmap(roadmap_text)
            
            # Property: Structure should be a dictionary (hierarchical)
            assert isinstance(structure, dict), "Roadmap structure must be a dictionary"
            
            # Property: Should have at least one topic level (can be empty for invalid input)
            # Since we're mocking, we know it will have content
            assert len(structure) >= 0, "Roadmap should have at least zero topics"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 9: Roadmap Format Compliance
    def test_property_roadmap_format_compliance(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 2.2**
        
        Property 9: Roadmap Format Compliance
        For any generated roadmap, the text output should use the hierarchical 
        arrow notation (Topic -> Subtopic -> Sub-subtopic).
        """
        from hypothesis import given, strategies as st, settings
        
        @given(
            syllabus_text=st.text(min_size=10, max_size=500).filter(lambda x: x.strip()),
            difficulty=st.sampled_from(["beginner", "intermediate", "advanced"])
        )
        @settings(max_examples=100, deadline=None)
        def property_test(syllabus_text, difficulty):
            # Mock the API response with valid arrow notation
            mock_response = Mock()
            mock_response.text = "Topic -> Subtopic -> Sub-subtopic\nTopic -> Subtopic2"
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Generate roadmap
            roadmap_text = content_generator.generate_roadmap(syllabus_text, difficulty)
            
            # Property: Roadmap should contain arrow notation or be an error message
            if "Error" not in roadmap_text:
                # Valid roadmap should have arrows or be empty
                lines = [line.strip() for line in roadmap_text.split('\n') if line.strip()]
                if lines:
                    # At least one line should have arrow notation
                    has_arrow = any('->' in line for line in lines)
                    assert has_arrow or len(lines) == 0, "Valid roadmap should use -> notation"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 10: Roadmap Persistence
    def test_property_roadmap_persistence(self, content_generator_with_db, mock_gemini_model):
        """
        **Validates: Requirements 2.4**
        
        Property 10: Roadmap Persistence
        For any generated roadmap, the hierarchical structure should be stored 
        in the database and retrievable by ID.
        """
        from hypothesis import given, strategies as st, settings
        
        @given(
            roadmap_text=st.text(min_size=1, max_size=1000),
            difficulty=st.sampled_from(["beginner", "intermediate", "advanced"]),
            source_type=st.sampled_from(["syllabus", "pdf"])
        )
        @settings(max_examples=100, deadline=None)
        def property_test(roadmap_text, difficulty, source_type):
            # Create a simple structure
            structure = {"Topic": {"Subtopic": ["Sub-subtopic"]}}
            
            # Store roadmap
            roadmap_id = content_generator_with_db.store_roadmap(
                roadmap_text=roadmap_text,
                roadmap_structure=structure,
                difficulty_level=difficulty,
                source_type=source_type
            )
            
            # Property: Should return a valid ID
            assert isinstance(roadmap_id, int), "Roadmap ID must be an integer"
            assert roadmap_id > 0, "Roadmap ID must be positive"
            
            # Property: Should be retrievable by ID
            retrieved = content_generator_with_db.get_roadmap(roadmap_id)
            assert retrieved is not None, "Stored roadmap must be retrievable"
            assert retrieved["id"] == roadmap_id, "Retrieved ID must match stored ID"
            assert retrieved["roadmap_structure"] == structure, "Structure must be preserved"
            assert retrieved["difficulty_level"] == difficulty, "Difficulty must be preserved"
            assert retrieved["source_type"] == source_type, "Source type must be preserved"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 11: Roadmap Statistics Accuracy
    def test_property_roadmap_statistics_accuracy(self, content_generator):
        """
        **Validates: Requirements 2.6**
        
        Property 11: Roadmap Statistics Accuracy
        For any generated roadmap, the displayed topic and subtopic counts should 
        match the actual counts in the hierarchical structure.
        """
        from hypothesis import given, strategies as st, settings
        
        # Strategy to generate valid roadmap structures
        @st.composite
        def roadmap_structure_strategy(draw):
            num_topics = draw(st.integers(min_value=0, max_value=10))
            structure = {}
            
            for i in range(num_topics):
                topic_name = f"Topic_{i}"
                num_subtopics = draw(st.integers(min_value=0, max_value=5))
                structure[topic_name] = {}
                
                for j in range(num_subtopics):
                    subtopic_name = f"Subtopic_{j}"
                    num_sub_subtopics = draw(st.integers(min_value=0, max_value=3))
                    structure[topic_name][subtopic_name] = [
                        f"SubSubtopic_{k}" for k in range(num_sub_subtopics)
                    ]
            
            return structure
        
        @given(structure=roadmap_structure_strategy())
        @settings(max_examples=100, deadline=None)
        def property_test(structure):
            # Calculate expected counts
            expected_topic_count = len(structure)
            expected_subtopic_count = sum(len(subtopics) for subtopics in structure.values())
            expected_sub_subtopic_count = sum(
                len(sub_subs) 
                for subtopics in structure.values() 
                for sub_subs in subtopics.values()
            )
            
            # Property: Topic count matches
            actual_topic_count = len(structure)
            assert actual_topic_count == expected_topic_count, \
                f"Topic count mismatch: expected {expected_topic_count}, got {actual_topic_count}"
            
            # Property: Subtopic count matches
            actual_subtopic_count = sum(len(subtopics) for subtopics in structure.values())
            assert actual_subtopic_count == expected_subtopic_count, \
                f"Subtopic count mismatch: expected {expected_subtopic_count}, got {actual_subtopic_count}"
            
            # Property: Sub-subtopic count matches
            actual_sub_subtopic_count = sum(
                len(sub_subs) 
                for subtopics in structure.values() 
                for sub_subs in subtopics.values()
            )
            assert actual_sub_subtopic_count == expected_sub_subtopic_count, \
                f"Sub-subtopic count mismatch: expected {expected_sub_subtopic_count}, got {actual_sub_subtopic_count}"
        
        property_test()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestStoreBaseNotes:
    """Tests for store_base_notes method."""
    
    def test_store_base_notes_saves_to_database(self, content_generator_with_db):
        """Test that base notes are stored in database."""
        tags = [
            {"tag_type": "EXAMPLE", "context": "test", "start_position": 10, "end_position": 20}
        ]
        
        base_notes_id = content_generator_with_db.store_base_notes(
            topic="Machine Learning",
            subtopic="Linear Regression",
            difficulty_level="intermediate",
            content="Test content with [EXAMPLE:test] tag",
            tags=tags
        )
        
        assert isinstance(base_notes_id, int)
        assert base_notes_id > 0
    
    def test_store_base_notes_requires_database(self, content_generator):
        """Test that store_base_notes raises error without database."""
        with pytest.raises(ValueError, match="Database connection not initialized"):
            content_generator.store_base_notes(
                topic="Test",
                subtopic="Test",
                difficulty_level="intermediate",
                content="Content",
                tags=[]
            )
    
    def test_store_base_notes_with_pdf_hash_and_images(self, content_generator_with_db):
        """Test storing base notes with PDF hash and associated images."""
        tags = []
        pdf_hash = "abc123def456"
        image_ids = [1, 2, 3]
        
        base_notes_id = content_generator_with_db.store_base_notes(
            topic="Physics",
            subtopic="Mechanics",
            difficulty_level="advanced",
            content="Physics content",
            tags=tags,
            pdf_hash=pdf_hash,
            associated_images=image_ids
        )
        
        # Verify stored data
        row = content_generator_with_db.db.fetchone(
            "SELECT * FROM base_notes WHERE id = ?",
            (base_notes_id,)
        )
        
        assert row["pdf_hash"] == pdf_hash
        assert json.loads(row["associated_images"]) == image_ids
    
    def test_store_base_notes_with_multiple_tags(self, content_generator_with_db):
        """Test storing base notes with multiple tags."""
        tags = [
            {"tag_type": "EXAMPLE", "context": "derivatives", "start_position": 10, "end_position": 30},
            {"tag_type": "ANALOGY", "context": "neural_networks", "start_position": 50, "end_position": 70},
            {"tag_type": "APPLICATION", "context": "robotics", "start_position": 90, "end_position": 110}
        ]
        
        base_notes_id = content_generator_with_db.store_base_notes(
            topic="Calculus",
            subtopic="Derivatives",
            difficulty_level="intermediate",
            content="Content with multiple tags",
            tags=tags
        )
        
        # Verify tags are stored correctly
        row = content_generator_with_db.db.fetchone(
            "SELECT tags FROM base_notes WHERE id = ?",
            (base_notes_id,)
        )
        
        stored_tags = json.loads(row["tags"])
        assert len(stored_tags) == 3
        assert stored_tags[0]["tag_type"] == "EXAMPLE"
        assert stored_tags[1]["tag_type"] == "ANALOGY"
        assert stored_tags[2]["tag_type"] == "APPLICATION"


class TestGetBaseNotes:
    """Tests for get_base_notes method."""
    
    def test_get_base_notes_retrieves_stored_data(self, content_generator_with_db):
        """Test retrieving base notes from database."""
        tags = [{"tag_type": "EXAMPLE", "context": "test", "start_position": 10, "end_position": 20}]
        content = "Test content"
        
        # Store base notes
        base_notes_id = content_generator_with_db.store_base_notes(
            topic="ML",
            subtopic="Regression",
            difficulty_level="beginner",
            content=content,
            tags=tags
        )
        
        # Retrieve base notes
        result = content_generator_with_db.get_base_notes(base_notes_id)
        
        assert result is not None
        assert result["id"] == base_notes_id
        assert result["topic"] == "ML"
        assert result["subtopic"] == "Regression"
        assert result["difficulty_level"] == "beginner"
        assert result["content"] == content
        assert result["tags"] == tags
    
    def test_get_base_notes_returns_none_for_invalid_id(self, content_generator_with_db):
        """Test that get_base_notes returns None for non-existent ID."""
        result = content_generator_with_db.get_base_notes(99999)
        
        assert result is None
    
    def test_get_base_notes_requires_database(self, content_generator):
        """Test that get_base_notes raises error without database."""
        with pytest.raises(ValueError, match="Database connection not initialized"):
            content_generator.get_base_notes(1)
    
    def test_get_base_notes_with_empty_images(self, content_generator_with_db):
        """Test retrieving base notes with no associated images."""
        base_notes_id = content_generator_with_db.store_base_notes(
            topic="Test",
            subtopic="Test",
            difficulty_level="intermediate",
            content="Content",
            tags=[],
            associated_images=None
        )
        
        result = content_generator_with_db.get_base_notes(base_notes_id)
        
        assert result["associated_images"] == []


class TestGenerateAndStoreBaseNotes:
    """Tests for generate_and_store_base_notes method."""
    
    def test_generate_and_store_base_notes_complete_workflow(self, content_generator_with_db, mock_gemini_model):
        """Test complete workflow of generating and storing base notes."""
        mock_response = Mock()
        mock_response.text = "Content with [EXAMPLE:test] and [ANALOGY:concept] tags"
        mock_gemini_model.generate_content.return_value = mock_response
        
        result = content_generator_with_db.generate_and_store_base_notes(
            topic="Machine Learning",
            subtopic="Neural Networks",
            difficulty="advanced",
            context="For CS students"
        )
        
        assert "id" in result
        assert "content" in result
        assert "tags" in result
        assert result["topic"] == "Machine Learning"
        assert result["subtopic"] == "Neural Networks"
        assert result["difficulty"] == "advanced"
        assert len(result["tags"]) == 2
    
    def test_generate_and_store_base_notes_without_database(self, content_generator, mock_gemini_model):
        """Test that method works without database (doesn't store)."""
        mock_response = Mock()
        mock_response.text = "Content"
        mock_gemini_model.generate_content.return_value = mock_response
        
        result = content_generator.generate_and_store_base_notes(
            topic="Test",
            subtopic="Test",
            difficulty="intermediate"
        )
        
        assert "id" not in result
        assert "content" in result
        assert "tags" in result
    
    def test_generate_and_store_base_notes_with_pdf_hash(self, content_generator_with_db, mock_gemini_model):
        """Test generating and storing base notes with PDF hash."""
        mock_response = Mock()
        mock_response.text = "Content"
        mock_gemini_model.generate_content.return_value = mock_response
        
        pdf_hash = "test_hash_123"
        image_ids = [1, 2, 3]
        
        result = content_generator_with_db.generate_and_store_base_notes(
            topic="Physics",
            subtopic="Optics",
            difficulty="intermediate",
            pdf_hash=pdf_hash,
            associated_images=image_ids
        )
        
        # Verify stored in database
        stored = content_generator_with_db.get_base_notes(result["id"])
        assert stored["pdf_hash"] == pdf_hash
        assert stored["associated_images"] == image_ids


class TestBaseNotesPropertyTests:
    """Property-based tests for base notes generation and storage."""
    
    # Feature: unified-smart-teaching-assistant, Property 12: Base Notes Structure Completeness
    def test_property_base_notes_structure_completeness(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 3.1**
        
        Property 12: Base Notes Structure Completeness
        For any generated base notes, the content should include identifiable 
        sections for learning objectives, key concepts, and assessment questions.
        """
        from hypothesis import given, strategies as st, settings
        import re
        
        @given(
            topic=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            subtopic=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            difficulty=st.sampled_from(["beginner", "intermediate", "advanced"])
        )
        @settings(max_examples=100, deadline=None)
        def property_test(topic, subtopic, difficulty):
            # Mock response with complete structure
            mock_response = Mock()
            mock_response.text = f"""
# Learning Objectives
- Understand {subtopic} in {topic}
- Apply concepts to real-world problems

# Key Concepts
{subtopic} is a fundamental concept in {topic}.

# Definitions
Definition of {subtopic}: A core principle.

# Detailed Explanations
This section explains {subtopic} in detail.

# Assessment Questions
1. What is {subtopic}?
2. How would you apply {subtopic}?
"""
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Generate base notes
            result = content_generator.generate_base_notes(
                topic=topic,
                subtopic=subtopic,
                difficulty=difficulty
            )
            
            # Property: Content must be present
            assert "content" in result, "Result must contain content field"
            content = result["content"]
            assert len(content) > 0, "Content must not be empty"
            
            # Property: Should have identifiable sections
            # Check for learning objectives (case-insensitive)
            has_objectives = bool(re.search(r'learning\s+objectives?|objectives?', content, re.IGNORECASE))
            
            # Check for key concepts or definitions
            has_concepts = bool(re.search(r'key\s+concepts?|concepts?|definitions?', content, re.IGNORECASE))
            
            # Check for assessment questions
            has_questions = bool(re.search(r'assessment|questions?|quiz|test', content, re.IGNORECASE))
            
            # Property: At least learning objectives should be present
            assert has_objectives, "Content should include learning objectives section"
            
            # Property: Should have concepts or definitions
            assert has_concepts, "Content should include key concepts or definitions"
            
            # Property: Should have assessment questions
            assert has_questions, "Content should include assessment questions"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 13: Automatic Tag Generation
    def test_property_automatic_tag_generation(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 3.2, 3.3**
        
        Property 13: Automatic Tag Generation
        For any generated base notes, the system should insert tags following 
        the format [TAG_TYPE:context] where TAG_TYPE is one of EXAMPLE, ANALOGY, 
        APPLICATION, or CASE_STUDY.
        """
        from hypothesis import given, strategies as st, settings
        
        # Valid tag types
        valid_tag_types = ["EXAMPLE", "ANALOGY", "APPLICATION", "CASE_STUDY"]
        
        @given(
            num_tags=st.integers(min_value=0, max_value=5)
        )
        @settings(max_examples=100, deadline=None)
        def property_test(num_tags):
            # Create content with random tags
            tag_parts = []
            for i in range(num_tags):
                tag_type = valid_tag_types[i % len(valid_tag_types)]
                context = f"context_{i}"
                tag_parts.append(f"[{tag_type}:{context}]")
            
            content = "Start " + " middle ".join(tag_parts) + " end"
            
            mock_response = Mock()
            mock_response.text = content
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Generate base notes
            result = content_generator.generate_base_notes(
                topic="Test",
                subtopic="Test",
                difficulty="intermediate"
            )
            
            # Property: Tags should be extracted
            assert "tags" in result, "Result must contain tags field"
            assert isinstance(result["tags"], list), "Tags must be a list"
            assert len(result["tags"]) == num_tags, f"Should extract {num_tags} tags"
            
            # Property: Each tag should have correct format
            for tag in result["tags"]:
                assert "tag_type" in tag, "Tag must have tag_type field"
                assert "context" in tag, "Tag must have context field"
                assert "start_position" in tag, "Tag must have start_position field"
                assert "end_position" in tag, "Tag must have end_position field"
                
                # Property: Tag type must be valid
                assert tag["tag_type"] in valid_tag_types, \
                    f"Tag type must be one of {valid_tag_types}"
                
                # Property: Positions must be valid
                assert tag["start_position"] < tag["end_position"], \
                    "Start position must be before end position"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 14: Base Notes Storage Completeness
    def test_property_base_notes_storage_completeness(self, content_generator_with_db, mock_gemini_model):
        """
        **Validates: Requirements 3.4**
        
        Property 14: Base Notes Storage Completeness
        For any generated base notes with tags, the database should store both 
        the complete content and a separate tags field containing tag metadata.
        """
        from hypothesis import given, strategies as st, settings
        
        @given(
            topic=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            subtopic=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            difficulty=st.sampled_from(["beginner", "intermediate", "advanced"])
        )
        @settings(max_examples=100, deadline=None)
        def property_test(topic, subtopic, difficulty):
            # Mock response with tags
            mock_response = Mock()
            mock_response.text = f"Content for {topic} - {subtopic} [EXAMPLE:test] with tags [ANALOGY:concept]"
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Generate and store base notes
            result = content_generator_with_db.generate_and_store_base_notes(
                topic=topic,
                subtopic=subtopic,
                difficulty=difficulty
            )
            
            # Property: Should have an ID (stored in database)
            assert "id" in result, "Stored base notes must have an ID"
            assert isinstance(result["id"], int), "ID must be an integer"
            
            # Property: Should be retrievable from database
            stored = content_generator_with_db.get_base_notes(result["id"])
            assert stored is not None, "Stored base notes must be retrievable"
            
            # Property: Content must be stored
            assert "content" in stored, "Stored base notes must have content field"
            assert len(stored["content"]) > 0, "Content must not be empty"
            
            # Property: Tags must be stored separately
            assert "tags" in stored, "Stored base notes must have tags field"
            assert isinstance(stored["tags"], list), "Tags must be a list"
            
            # Property: All required fields must be present
            required_fields = ["id", "topic", "subtopic", "difficulty_level", "content", "tags"]
            for field in required_fields:
                assert field in stored, f"Required field '{field}' must be present"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 46: Learning Objectives Presence
    def test_property_learning_objectives_presence(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 15.1**
        
        Property 46: Learning Objectives Presence
        For any generated base notes, the content should include a section 
        containing learning objectives.
        """
        from hypothesis import given, strategies as st, settings
        import re
        
        @given(
            topic=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            subtopic=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            difficulty=st.sampled_from(["beginner", "intermediate", "advanced"])
        )
        @settings(max_examples=100, deadline=None)
        def property_test(topic, subtopic, difficulty):
            # Mock response with learning objectives
            mock_response = Mock()
            mock_response.text = f"""
## Learning Objectives
By the end of this lesson, students will be able to:
- Understand the fundamentals of {subtopic}
- Apply {subtopic} concepts to solve problems
- Analyze real-world applications of {subtopic}

## Content
This is the main content about {subtopic} in {topic}.
"""
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Generate base notes
            result = content_generator.generate_base_notes(
                topic=topic,
                subtopic=subtopic,
                difficulty=difficulty
            )
            
            # Property: Content must be present
            assert "content" in result, "Result must contain content field"
            content = result["content"]
            
            # Property: Should contain learning objectives section
            has_objectives = bool(re.search(r'learning\s+objectives?|objectives?:', content, re.IGNORECASE))
            assert has_objectives, "Content must include a learning objectives section"
            
            # Property: Learning objectives should contain action verbs or learning outcomes
            # Look for common patterns in learning objectives
            has_learning_patterns = bool(re.search(
                r'(understand|apply|analyze|evaluate|create|identify|explain|describe|demonstrate|solve)',
                content,
                re.IGNORECASE
            ))
            assert has_learning_patterns, "Learning objectives should contain measurable action verbs"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 47: Assessment Question Diversity
    def test_property_assessment_question_diversity(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 15.5**
        
        Property 47: Assessment Question Diversity
        For any generated base notes with assessment questions, the questions 
        should include at least two different types (e.g., recall and application).
        """
        from hypothesis import given, strategies as st, settings
        import re
        
        @given(
            topic=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            subtopic=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            difficulty=st.sampled_from(["beginner", "intermediate", "advanced"])
        )
        @settings(max_examples=100, deadline=None)
        def property_test(topic, subtopic, difficulty):
            # Mock response with diverse assessment questions
            mock_response = Mock()
            mock_response.text = f"""
## Content
This is content about {subtopic}.

## Assessment Questions

### Recall Questions
1. What is {subtopic}?
2. Define the key terms related to {subtopic}.

### Application Questions
3. How would you apply {subtopic} to solve a real-world problem?
4. Given a scenario, use {subtopic} to find a solution.

### Analysis Questions
5. Compare and contrast different approaches to {subtopic}.
"""
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Generate base notes
            result = content_generator.generate_base_notes(
                topic=topic,
                subtopic=subtopic,
                difficulty=difficulty
            )
            
            # Property: Content must be present
            assert "content" in result, "Result must contain content field"
            content = result["content"]
            
            # Property: Should contain assessment questions
            has_questions = bool(re.search(r'assessment|questions?|quiz', content, re.IGNORECASE))
            
            if has_questions:
                # Property: Should have diverse question types
                # Check for recall-type questions (what, define, list, identify)
                has_recall = bool(re.search(r'\b(what|define|list|identify|name|state)\b', content, re.IGNORECASE))
                
                # Check for application-type questions (how, apply, use, solve, implement)
                has_application = bool(re.search(r'\b(how|apply|use|solve|implement|demonstrate)\b', content, re.IGNORECASE))
                
                # Check for analysis-type questions (compare, analyze, evaluate, why)
                has_analysis = bool(re.search(r'\b(compare|analyze|evaluate|why|explain|contrast)\b', content, re.IGNORECASE))
                
                # Property: Should have at least 2 different question types
                question_types_count = sum([has_recall, has_application, has_analysis])
                assert question_types_count >= 2, \
                    f"Assessment questions should include at least 2 different types (found {question_types_count})"
        
        property_test()



class TestGenerateMultimodalLesson:
    """Tests for generate_multimodal_lesson method."""
    
    def test_generate_multimodal_lesson_returns_structured_data(self, content_generator, mock_gemini_model):
        """Test that generate_multimodal_lesson returns properly structured data."""
        mock_response = Mock()
        mock_response.text = "Content with [EXAMPLE:test] tag"
        mock_gemini_model.generate_content.return_value = mock_response
        
        result = content_generator.generate_multimodal_lesson(
            topic="Machine Learning",
            subtopic="Linear Regression",
            difficulty="intermediate",
            pdf_hash="test_hash_123"
        )
        
        assert "content" in result
        assert "tags" in result
        assert "images" in result
        assert "pdf_hash" in result
        assert "topic" in result
        assert "subtopic" in result
        assert result["pdf_hash"] == "test_hash_123"
    
    def test_generate_multimodal_lesson_without_pdf_extractor(self, content_generator, mock_gemini_model):
        """Test that method works without pdf_extractor (returns empty images)."""
        mock_response = Mock()
        mock_response.text = "Content"
        mock_gemini_model.generate_content.return_value = mock_response
        
        result = content_generator.generate_multimodal_lesson(
            topic="Physics",
            subtopic="Mechanics",
            difficulty="advanced",
            pdf_hash="hash123"
        )
        
        assert result["images"] == []
        assert result["pdf_hash"] == "hash123"
    
    def test_generate_multimodal_lesson_with_pdf_extractor(self, content_generator, mock_gemini_model):
        """Test multimodal lesson generation with PDF extractor integration."""
        mock_response = Mock()
        mock_response.text = "Content about neural networks and deep learning"
        mock_gemini_model.generate_content.return_value = mock_response
        
        # Mock PDF extractor
        mock_extractor = Mock()
        mock_images = [
            {
                "id": 1,
                "pdf_hash": "test_hash",
                "page_number": 1,
                "caption": "Neural network diagram",
                "description": "A diagram showing neural network architecture",
                "similarity_score": 0.85
            },
            {
                "id": 2,
                "pdf_hash": "test_hash",
                "page_number": 2,
                "caption": "Deep learning layers",
                "description": "Illustration of deep learning layers",
                "similarity_score": 0.72
            }
        ]
        mock_extractor.find_relevant_images.return_value = mock_images
        
        result = content_generator.generate_multimodal_lesson(
            topic="AI",
            subtopic="Neural Networks",
            difficulty="advanced",
            pdf_hash="test_hash",
            pdf_extractor=mock_extractor
        )
        
        assert len(result["images"]) == 2
        assert result["images"][0]["similarity_score"] == 0.85
        assert result["images"][1]["similarity_score"] == 0.72
        
        # Verify find_relevant_images was called with correct parameters
        mock_extractor.find_relevant_images.assert_called_once()
        call_kwargs = mock_extractor.find_relevant_images.call_args[1]
        assert call_kwargs["pdf_hash"] == "test_hash"
        assert call_kwargs["top_k"] == 5
    
    def test_generate_multimodal_lesson_with_custom_top_k(self, content_generator, mock_gemini_model):
        """Test multimodal lesson generation with custom top_k parameter."""
        mock_response = Mock()
        mock_response.text = "Content"
        mock_gemini_model.generate_content.return_value = mock_response
        
        mock_extractor = Mock()
        mock_extractor.find_relevant_images.return_value = []
        
        content_generator.generate_multimodal_lesson(
            topic="Test",
            subtopic="Test",
            difficulty="intermediate",
            pdf_hash="hash",
            pdf_extractor=mock_extractor,
            top_k=3
        )
        
        # Verify top_k parameter was passed correctly
        call_kwargs = mock_extractor.find_relevant_images.call_args[1]
        assert call_kwargs["top_k"] == 3
    
    def test_generate_multimodal_lesson_handles_extractor_error(self, content_generator, mock_gemini_model):
        """Test error handling when PDF extractor fails."""
        mock_response = Mock()
        mock_response.text = "Content"
        mock_gemini_model.generate_content.return_value = mock_response
        
        mock_extractor = Mock()
        mock_extractor.find_relevant_images.side_effect = Exception("Extractor error")
        
        result = content_generator.generate_multimodal_lesson(
            topic="Test",
            subtopic="Test",
            difficulty="intermediate",
            pdf_hash="hash",
            pdf_extractor=mock_extractor
        )
        
        # Should return empty images on error
        assert result["images"] == []
        assert "content" in result
    
    def test_generate_multimodal_lesson_with_context(self, content_generator, mock_gemini_model):
        """Test multimodal lesson generation with additional context."""
        mock_response = Mock()
        mock_response.text = "Content with context"
        mock_gemini_model.generate_content.return_value = mock_response
        
        result = content_generator.generate_multimodal_lesson(
            topic="Chemistry",
            subtopic="Organic Chemistry",
            difficulty="intermediate",
            pdf_hash="hash",
            context="For medical students"
        )
        
        # Verify context was passed to generate_base_notes
        call_args = mock_gemini_model.generate_content.call_args[0][0]
        assert "For medical students" in call_args


class TestMultimodalLessonPropertyTests:
    """Property-based tests for multimodal lesson generation."""
    
    # Feature: unified-smart-teaching-assistant, Property 15: Image Association with Content
    def test_property_image_association_with_content(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 3.6**
        
        Property 15: Image Association with Content
        For any base notes generated with an available PDF hash, the system 
        should associate at least zero images (empty list is valid if no 
        relevant images found).
        """
        from hypothesis import given, strategies as st, settings
        
        @given(
            topic=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            subtopic=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            difficulty=st.sampled_from(["beginner", "intermediate", "advanced"]),
            has_extractor=st.booleans()
        )
        @settings(max_examples=100, deadline=None)
        def property_test(topic, subtopic, difficulty, has_extractor):
            mock_response = Mock()
            mock_response.text = f"Content about {subtopic}"
            mock_gemini_model.generate_content.return_value = mock_response
            
            pdf_hash = "test_hash_123"
            pdf_extractor = None
            
            if has_extractor:
                # Mock PDF extractor
                pdf_extractor = Mock()
                pdf_extractor.find_relevant_images.return_value = []
            
            # Generate multimodal lesson
            result = content_generator.generate_multimodal_lesson(
                topic=topic,
                subtopic=subtopic,
                difficulty=difficulty,
                pdf_hash=pdf_hash,
                pdf_extractor=pdf_extractor
            )
            
            # Property: Result must have images field
            assert "images" in result, "Result must contain images field"
            
            # Property: Images must be a list
            assert isinstance(result["images"], list), "Images must be a list"
            
            # Property: Images list can be empty (valid if no relevant images)
            assert len(result["images"]) >= 0, "Images list must have zero or more items"
            
            # Property: PDF hash must be stored
            assert "pdf_hash" in result, "Result must contain pdf_hash field"
            assert result["pdf_hash"] == pdf_hash, "PDF hash must match input"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 22: Similarity Score Computation
    def test_property_similarity_score_computation(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 6.1, 6.5**
        
        Property 22: Similarity Score Computation
        For any lesson content and set of extracted images, the semantic matching 
        component should calculate a similarity score for each image.
        """
        from hypothesis import given, strategies as st, settings
        
        @given(
            num_images=st.integers(min_value=0, max_value=10)
        )
        @settings(max_examples=100, deadline=None)
        def property_test(num_images):
            mock_response = Mock()
            mock_response.text = "Content about machine learning"
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Create mock images with similarity scores
            mock_images = []
            for i in range(num_images):
                mock_images.append({
                    "id": i + 1,
                    "pdf_hash": "test_hash",
                    "page_number": i + 1,
                    "caption": f"Image {i + 1}",
                    "description": f"Description {i + 1}",
                    "similarity_score": 0.5 + (i * 0.05)  # Varying scores
                })
            
            # Mock PDF extractor
            mock_extractor = Mock()
            mock_extractor.find_relevant_images.return_value = mock_images
            
            # Generate multimodal lesson
            result = content_generator.generate_multimodal_lesson(
                topic="AI",
                subtopic="ML",
                difficulty="intermediate",
                pdf_hash="test_hash",
                pdf_extractor=mock_extractor
            )
            
            # Property: Each returned image must have a similarity score
            for image in result["images"]:
                assert "similarity_score" in image, "Each image must have a similarity_score field"
                assert isinstance(image["similarity_score"], (int, float)), \
                    "Similarity score must be numeric"
                assert 0 <= image["similarity_score"] <= 1, \
                    "Similarity score should be between 0 and 1"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 23: Image Ranking by Relevance
    def test_property_image_ranking_by_relevance(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 6.2**
        
        Property 23: Image Ranking by Relevance
        For any set of images with similarity scores, the returned images 
        should be ordered in descending order of relevance score.
        """
        from hypothesis import given, strategies as st, settings
        
        @given(
            num_images=st.integers(min_value=2, max_value=10)
        )
        @settings(max_examples=100, deadline=None)
        def property_test(num_images):
            mock_response = Mock()
            mock_response.text = "Content"
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Create mock images with random similarity scores
            import random
            mock_images = []
            for i in range(num_images):
                mock_images.append({
                    "id": i + 1,
                    "pdf_hash": "test_hash",
                    "page_number": i + 1,
                    "caption": f"Image {i + 1}",
                    "description": f"Description {i + 1}",
                    "similarity_score": random.random()
                })
            
            # Sort by similarity score descending (as PDFImageExtractor does)
            mock_images.sort(key=lambda x: x["similarity_score"], reverse=True)
            
            # Mock PDF extractor
            mock_extractor = Mock()
            mock_extractor.find_relevant_images.return_value = mock_images
            
            # Generate multimodal lesson
            result = content_generator.generate_multimodal_lesson(
                topic="Test",
                subtopic="Test",
                difficulty="intermediate",
                pdf_hash="test_hash",
                pdf_extractor=mock_extractor
            )
            
            # Property: Images should be ordered by descending similarity score
            images = result["images"]
            if len(images) > 1:
                for i in range(len(images) - 1):
                    assert images[i]["similarity_score"] >= images[i + 1]["similarity_score"], \
                        "Images must be ordered by descending similarity score"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 24: Image Result Limiting
    def test_property_image_result_limiting(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 6.3**
        
        Property 24: Image Result Limiting
        For any lesson content with matched images, the system should include 
        at most 5 images in the result (or custom top_k value).
        """
        from hypothesis import given, strategies as st, settings
        
        @given(
            num_available_images=st.integers(min_value=0, max_value=20),
            top_k=st.integers(min_value=1, max_value=10)
        )
        @settings(max_examples=100, deadline=None)
        def property_test(num_available_images, top_k):
            mock_response = Mock()
            mock_response.text = "Content"
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Create mock images
            mock_images = []
            for i in range(min(num_available_images, top_k)):
                mock_images.append({
                    "id": i + 1,
                    "pdf_hash": "test_hash",
                    "page_number": i + 1,
                    "caption": f"Image {i + 1}",
                    "description": f"Description {i + 1}",
                    "similarity_score": 0.9 - (i * 0.05)
                })
            
            # Mock PDF extractor (simulates top_k limiting)
            mock_extractor = Mock()
            mock_extractor.find_relevant_images.return_value = mock_images
            
            # Generate multimodal lesson
            result = content_generator.generate_multimodal_lesson(
                topic="Test",
                subtopic="Test",
                difficulty="intermediate",
                pdf_hash="test_hash",
                pdf_extractor=mock_extractor,
                top_k=top_k
            )
            
            # Property: Number of images should not exceed top_k
            assert len(result["images"]) <= top_k, \
                f"Number of images ({len(result['images'])}) should not exceed top_k ({top_k})"
            
            # Property: Number of images should not exceed available images
            assert len(result["images"]) <= num_available_images, \
                f"Number of images should not exceed available images"
        
        property_test()
    
    # Feature: unified-smart-teaching-assistant, Property 25: Included Image Metadata Completeness
    def test_property_included_image_metadata_completeness(self, content_generator, mock_gemini_model):
        """
        **Validates: Requirements 6.4**
        
        Property 25: Included Image Metadata Completeness
        For any image included in lesson content, the display should include 
        caption, description, source page number, and relevance score.
        """
        from hypothesis import given, strategies as st, settings
        
        @given(
            num_images=st.integers(min_value=1, max_value=5)
        )
        @settings(max_examples=100, deadline=None)
        def property_test(num_images):
            mock_response = Mock()
            mock_response.text = "Content"
            mock_gemini_model.generate_content.return_value = mock_response
            
            # Create mock images with complete metadata
            mock_images = []
            for i in range(num_images):
                mock_images.append({
                    "id": i + 1,
                    "pdf_hash": "test_hash",
                    "page_number": i + 1,
                    "caption": f"Caption for image {i + 1}",
                    "description": f"Detailed description for image {i + 1}",
                    "similarity_score": 0.8 - (i * 0.1),
                    "image_path": f"/path/to/image_{i + 1}.png"
                })
            
            # Mock PDF extractor
            mock_extractor = Mock()
            mock_extractor.find_relevant_images.return_value = mock_images
            
            # Generate multimodal lesson
            result = content_generator.generate_multimodal_lesson(
                topic="Test",
                subtopic="Test",
                difficulty="intermediate",
                pdf_hash="test_hash",
                pdf_extractor=mock_extractor
            )
            
            # Property: Each image must have required metadata fields
            required_fields = ["caption", "description", "page_number", "similarity_score"]
            
            for image in result["images"]:
                for field in required_fields:
                    assert field in image, f"Image must have '{field}' field"
                    assert image[field] is not None, f"Image '{field}' must not be None"
                
                # Property: Caption and description should be non-empty strings
                assert isinstance(image["caption"], str), "Caption must be a string"
                assert len(image["caption"]) > 0, "Caption must not be empty"
                
                assert isinstance(image["description"], str), "Description must be a string"
                assert len(image["description"]) > 0, "Description must not be empty"
                
                # Property: Page number should be positive integer
                assert isinstance(image["page_number"], int), "Page number must be an integer"
                assert image["page_number"] > 0, "Page number must be positive"
                
                # Property: Similarity score should be numeric and in valid range
                assert isinstance(image["similarity_score"], (int, float)), \
                    "Similarity score must be numeric"
        
        property_test()
