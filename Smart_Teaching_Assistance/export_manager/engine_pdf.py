"""
PDF generation engine for ExportManager using ReportLab.
"""

import re
import os
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import logging

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, PageBreak,
        Table, TableStyle, Image as RLImage, XPreformatted
    )
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib import colors
    from reportlab.platypus.tableofcontents import TableOfContents
except ImportError:
    # We delay raising this until actual use to follow the existing pattern
    pass

from .utils import (
    clean_markdown, topic_display_label, looks_like_json, 
    sanitize_json_content, download_image_to_temp, 
    pdf_safe, escape_xml, remove_tags, md_to_reportlab
)

logger = logging.getLogger(__name__)

class PdfEngine:
    def __init__(self, db):
        self.db = db

    def export_full_course(
        self,
        subject: str,
        all_notes_data: List[Dict],
        answer_key: Optional[str] = None,
        output_path: str = None,
        include_title_page: bool = True,
        chapter_summaries: Optional[Dict] = None,
        glossary: Optional[List[Dict]] = None
    ) -> str:
        try:
            from reportlab.lib.pagesizes import letter
        except ImportError:
            raise ImportError("reportlab is required for PDF export. Install with 'pip install reportlab'")

        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_subject = re.sub(r'[^\w\s-]', '', subject)[:50]
            filename = f"Full_Course_{safe_subject}_{timestamp}.pdf"
            output_path = Path("outputs") / filename
        else:
            output_path = Path(output_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(str(output_path), pagesize=letter)
        styles = getSampleStyleSheet()
        
        # Define extra styles
        if 'CustomCode' not in styles:
            styles.add(ParagraphStyle(
                name='CustomCode', parent=styles['Normal'], 
                fontName='Courier', fontSize=9, leftIndent=0, 
                leading=12, spaceBefore=0, spaceAfter=0
            ))
        if 'Math' not in styles:
            styles.add(ParagraphStyle(
                name='Math', parent=styles['Normal'], 
                alignment=TA_CENTER, fontName='Times-Italic', 
                fontSize=12, spaceBefore=12, spaceAfter=12,
                leading=16
            ))
        if 'BoxContent' not in styles:
            styles.add(ParagraphStyle(
                name='BoxContent', parent=styles['Normal'], 
                leftIndent=10, leading=14, spaceBefore=4, spaceAfter=4
            ))
        if 'ListItem' not in styles:
            styles.add(ParagraphStyle(
                name='ListItem', parent=styles['Normal'],
                leftIndent=20, firstLineIndent=-10, spaceBefore=2, spaceAfter=2
            ))

        toc_styles = []
        for lvl in range(4):
            sname = f'TOCLevel{lvl}'
            if sname not in styles:
                styles.add(ParagraphStyle(
                    name=sname, parent=styles['Normal'],
                    fontSize=12 - lvl, leading=18 - lvl * 2,
                    leftIndent=20 * lvl, spaceBefore=4 if lvl == 0 else 1,
                    spaceAfter=4 if lvl == 0 else 1,
                    textColor=colors.HexColor("#1A5276") if lvl == 0 else colors.black,
                ))
            toc_styles.append(styles[sname])

        story = []
        toc = TableOfContents()
        toc.levelStyles = toc_styles

        if include_title_page:
            custom_title = ParagraphStyle('CoverTitle', parent=styles['Title'], fontSize=36, leading=42, spaceAfter=20, textColor=colors.HexColor("#1A5276"))
            story.append(Paragraph(pdf_safe(subject.upper()), custom_title))
            story.append(Spacer(1, 0.2*inch))
            sub_style = ParagraphStyle('CoverSub', parent=styles['Normal'], fontSize=16, alignment=TA_CENTER, textColor=colors.grey)
            story.append(Paragraph(pdf_safe("Comprehensive Study Guide & Instructor Manual"), sub_style))
            story.append(Spacer(1, 4*inch))
            story.append(Paragraph(f"<b>Generated:</b> {datetime.now().strftime('%B %d, %Y')}", styles['Normal']))
            story.append(PageBreak())
            story.append(Paragraph("Table of Contents", styles['Heading1']))
            story.append(Spacer(1, 0.2*inch))
            story.append(toc)
            story.append(PageBreak())
        
        temp_files_to_cleanup = []
        
        for i, topic in enumerate(all_notes_data):
            t_id = topic.get('id', '')
            depth = t_id.count('.')
            if depth == 0 and i > 0:
                story.append(PageBreak())

            title = clean_markdown(topic.get('title', 'Untitled'))
            display = topic_display_label(t_id, title)
            heading_style = f'Heading{min(depth + 1, 4)}'
            story.append(Paragraph(f'<a name="topic_{i}"></a>{escape_xml(display)}', styles[heading_style]))
            story.append(Spacer(1, 0.15*inch))
            
            for el in topic.get("elements", []):
                self._add_element(story, styles, el, depth, temp_files_to_cleanup)
            
        if answer_key:
            story.append(PageBreak())
            story.append(Paragraph("Answer Key for Practice Problems", styles['Heading1']))
            story.append(Spacer(1, 0.1*inch))
            self._add_markdown(story, styles, answer_key)

        if chapter_summaries:
            story.append(PageBreak())
            story.append(Paragraph("Chapter Summaries", styles['Heading1']))
            for ch_id, ch_data in chapter_summaries.items():
                ch_title = ch_data.get("title", ch_id)
                story.append(Paragraph(pdf_safe(ch_title), styles['Heading2']))
                summary_text = ch_data.get("summary", "")
                if summary_text:
                    story.append(Paragraph(pdf_safe(summary_text), styles['Normal']))
                key_concepts = ch_data.get("key_concepts", [])
                for concept in key_concepts:
                    story.append(Paragraph(f"• {pdf_safe(str(concept))}", styles['Normal']))
                outcomes = ch_data.get("learning_outcomes", [])
                for outcome in outcomes:
                    story.append(Paragraph(f"• {pdf_safe(str(outcome))}", styles['Normal']))
                connection = ch_data.get("connections_to_next", "")
                if connection:
                    story.append(Paragraph(f"<i>→ {pdf_safe(connection)}</i>", styles['Normal']))

        try:
            def _afterFlowable(flowable):
                if isinstance(flowable, Paragraph):
                    style_name = flowable.style.name
                    for lvl, hname in enumerate(('Heading1', 'Heading2', 'Heading3', 'Heading4')):
                        if style_name == hname:
                            text = re.sub(r'<[^>]+>', '', flowable.getPlainText())
                            toc.addEntry(lvl, escape_xml(text), doc.page)
                            break
            doc.afterFlowable = _afterFlowable
            doc.multiBuild(story)
        finally:
            for tf in temp_files_to_cleanup:
                try: os.remove(tf)
                except: pass
        return str(output_path)

    def _add_element(self, story, styles, el, depth, temp_files):
        etype = el.get("type")
        content = el.get("content", "")
        if etype == "text" and looks_like_json(content):
            content = sanitize_json_content(content)
        
        if etype == "heading":
            level = el.get("level", 2)
            adj_level = min(level + depth + 1, 4)
            story.append(Paragraph(md_to_reportlab(content), styles[f'Heading{adj_level}']))
        elif etype == "text":
            # Detect and handle markdown tables within text (improved regex)
            parts = re.split(r'((?:^|\n)\|.*\|(?:(?:\n\|.*\|)+|(?:\r\n\|.*\|)+))', content)
            for part in parts:
                if not part: continue
                if re.match(r'^\n?\|.*\|', part.strip()):
                    self._add_table_from_md(story, styles, part)
                elif part.strip():
                    self._add_text_with_lists(story, styles, part)
        elif etype == "code":
            clean_code = content.strip().replace('```', '')
            # Use XPreformatted for code to preserve whitespace
            p = XPreformatted(escape_xml(clean_code), styles['CustomCode'])
            data = [[p]]
            t = Table(data, colWidths=[6.5*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), colors.whitesmoke),
                ('BOX', (0,0), (-1,-1), 0.5, colors.grey),
                ('LEFTPADDING', (0,0), (-1,-1), 10),
                ('RIGHTPADDING', (0,0), (-1,-1), 10),
                ('TOPPADDING', (0,0), (-1,-1), 10),
                ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ]))
            story.append(Spacer(1, 0.1*inch))
            story.append(t)
            story.append(Spacer(1, 0.1*inch))
        elif etype == "equation":
            clean_math = content.replace('$', '').strip()
            # Wrap equation in a subtle box
            inner_text = md_to_reportlab(clean_math)
            data = [[Paragraph(inner_text, styles['Math'])]]
            t = Table(data, colWidths=[6.0*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8F9F9")),
                ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#D5DBDB")),
                ('TOPPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ]))
            story.append(Spacer(1, 0.1*inch))
            story.append(t)
            story.append(Spacer(1, 0.1*inch))
        elif etype == "callout":
            ctype = el.get("callout_type", "Note")
            color = colors.whitesmoke
            if "Takeaway" in ctype: color = colors.HexColor("#D9EAD3")
            elif "Analogy" in ctype: color = colors.HexColor("#FFF2CC")
            elif "Understanding" in ctype: color = colors.HexColor("#DEEBF7")
            elif "Practice" in ctype: color = colors.HexColor("#FAD7A0")
            inner_text = f"<b>{escape_xml(ctype)}</b><br />{md_to_reportlab(content)}"
            data = [[Paragraph(inner_text, styles['BoxContent'])]]
            t = Table(data, colWidths=[6.5*inch])
            t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), color), ('BOX', (0,0), (-1,-1), 0.5, colors.grey)]))
            # Keep callout cards visually separated from surrounding body text.
            story.append(Spacer(1, 0.12*inch))
            story.append(t)
            story.append(Spacer(1, 0.12*inch))
        elif etype == "image_placeholder":
            local_img = el.get("image_local_path", "")
            image_url = el.get("image_url")
            temp_img_path = None
            if local_img and os.path.isfile(local_img):
                temp_img_path = local_img
            elif image_url:
                temp_img_path = download_image_to_temp(image_url)
                if temp_img_path: temp_files.append(temp_img_path)
            if temp_img_path:
                try:
                    img = RLImage(temp_img_path, width=5.5*inch, height=3.5*inch, kind='proportional')
                    story.append(img)
                    if el.get("caption"):
                        story.append(Paragraph(f"<font color='gray'><i>Figure: {escape_xml(el.get('caption'))}</i></font>", styles['Normal']))
                except Exception as e:
                    logger.error(f"Failed to insert image into PDF: {e}")
        elif etype == "figure_block":
            # PDF-Extract-Kit figure auto-placed in the notes via RAG.
            # image_path is an absolute path to a PNG the ingest pipeline
            # already cropped from the user's PDF -- no download needed.
            # The caption is the exact caption the pipeline assigned, so
            # the printed figure matches the source verbatim.
            img_path = el.get("image_path", "")
            caption = el.get("caption", "") or "Figure"
            description = el.get("description", "")
            page = el.get("page")
            if img_path and os.path.isfile(img_path):
                try:
                    img = RLImage(img_path, width=5.0*inch, height=3.5*inch, kind='proportional')
                    story.append(Spacer(1, 0.12*inch))
                    story.append(img)
                    page_tag = f" (extracted from p.{page+1})" if isinstance(page, int) else ""
                    story.append(Paragraph(
                        f"<font color='gray'><i>Figure: {escape_xml(caption)}{escape_xml(page_tag)}</i></font>",
                        styles['Normal']
                    ))
                    if description:
                        story.append(Paragraph(
                            f"<font color='dimgray'><i>{escape_xml(description)}</i></font>",
                            styles['Normal']
                        ))
                    story.append(Spacer(1, 0.12*inch))
                except Exception as e:
                    logger.error(f"Failed to insert figure_block into PDF: {e}")
            else:
                # Image file went missing (cleaned up, etc.) -- still
                # print the caption so the document is informative.
                page_tag = f" (p.{page+1})" if isinstance(page, int) else ""
                story.append(Paragraph(
                    f"<font color='gray'>[Figure: {escape_xml(caption)}{escape_xml(page_tag)} - image file not available]</font>",
                    styles['Normal']
                ))
        elif etype == "source_citation":
            p = Paragraph(f"<font color='gray'><i>Source: {pdf_safe(content)}</i></font>", styles['Normal'])
            p.hAlign = 'RIGHT'
            story.append(p)
        elif etype == "table":
            rows_data = []
            for r in content.split('\n'):
                if '---' in r or not r.strip(): continue
                cells = [Paragraph(pdf_safe(c.strip()), styles['BoxContent']) for c in r.split('|') if c.strip() or r.startswith('|')]
                if cells: rows_data.append(cells)
            if rows_data:
                t = Table(rows_data, hAlign='LEFT', colWidths=[6.5*inch/len(rows_data[0])] * len(rows_data[0]))
                t.setStyle(TableStyle([
                    ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1A5276")),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
                    ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ]))
                story.append(t)

    def _add_table_from_md(self, story, styles, md_table):
        rows_data = []
        lines = [l.strip() for l in md_table.strip().split('\n')]
        for r in lines:
            if not r or re.match(r'^\|?\s*:?-+', r): continue
            cells = [Paragraph(md_to_reportlab(c.strip()), styles['BoxContent']) for c in r.split('|') if c.strip() or r.startswith('|')]
            if cells: rows_data.append(cells)
        if rows_data:
            cols = len(rows_data[0])
            t = Table(rows_data, hAlign='LEFT', colWidths=[6.5*inch/cols]*cols)
            t.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E5E7E9")),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('TOPPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ]))
            story.append(Spacer(1, 0.1*inch))
            story.append(t)
            story.append(Spacer(1, 0.1*inch))

    def _add_text_with_lists(self, story, styles, content):
        lines = content.split('\n')
        current_p = []
        
        def _flush_p():
            if current_p:
                text = "\n".join(current_p)
                story.append(Paragraph(md_to_reportlab(text), styles['Normal']))
                current_p.clear()

        for line in lines:
            match = re.match(r"^\s*([-\*\+•]\s+|\d+[\.\\)]\s+)(.*)", line)
            if match:
                _flush_p()
                bullet_char = match.group(1).strip()
                if bullet_char in ('-', '*', '+'): bullet_char = '•'
                text_content = match.group(2)
                story.append(Paragraph(f"{bullet_char} {md_to_reportlab(text_content)}", styles['ListItem']))
            elif not line.strip():
                _flush_p()
                story.append(Spacer(1, 0.05*inch))
            else:
                current_p.append(line)
        _flush_p()

    def _add_markdown(self, story, styles, markdown_text):
        lines = markdown_text.split('\n')
        current_p = []
        def _flush():
            if current_p:
                text = pdf_safe("\n".join(current_p))
                if text: story.append(Paragraph(text, styles['Normal']))
                current_p.clear()
        for line in lines:
            stripped = line.strip()
            if not stripped:
                _flush()
                continue
            hm = re.match(r'^(#{1,6})\s+(.*)', stripped)
            if hm:
                _flush()
                level = min(len(hm.group(1)) + 1, 4)
                story.append(Paragraph(md_to_reportlab(hm.group(2)), styles[f'Heading{level}']))
                continue
            bm = re.match(r'^([-*+•])\s+(.*)', stripped)
            if bm:
                _flush()
                # Use bullet point
                story.append(Paragraph(f"• {md_to_reportlab(bm.group(2))}", styles['ListItem']))
                continue
            nm = re.match(r'^(\d+)[.)]\s+(.*)', stripped)
            if nm:
                _flush()
                story.append(Paragraph(f"{nm.group(1)}. {md_to_reportlab(nm.group(2))}", styles['ListItem']))
                continue
            current_p.append(line)
        _flush()

    def export_single_note(
        self,
        content_data: Dict,
        audience: Optional[str] = None,
        include_tags: bool = False,
        output_path: str = None
    ) -> str:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, PageBreak
        from reportlab.lib.enums import TA_CENTER

        content = content_data['content']
        if not include_tags:
            content = remove_tags(content)
        
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_topic = re.sub(r'[^\w\s-]', '', content_data['topic'])[:30]
            safe_subtopic = re.sub(r'[^\w\s-]', '', content_data['subtopic'])[:30]
            filename = f"{safe_topic}_{safe_subtopic}_{timestamp}.pdf"
            output_path = Path("outputs") / filename
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(str(output_path), pagesize=letter)
        styles = getSampleStyleSheet()
        
        if 'NotesTitle' not in styles:
            styles.add(ParagraphStyle('NotesTitle', parent=styles['Title'], fontSize=20, spaceAfter=12, alignment=TA_CENTER))
        if 'NotesHeading' not in styles:
            styles.add(ParagraphStyle('NotesHeading', parent=styles['Heading1'], fontSize=14, spaceAfter=8))
        if 'CustomCode' not in styles:
            styles.add(ParagraphStyle(name='CustomCode', parent=styles['Normal'], fontName='Courier', fontSize=9, leftIndent=20, leading=11))
        
        body_style = styles['Normal']
        story = []
        
        story.append(Paragraph(escape_xml(f"{content_data['topic']}: {content_data['subtopic']}"), styles['NotesTitle']))
        story.append(Spacer(1, 12))
        story.append(Paragraph(escape_xml(f"Difficulty: {content_data['difficulty_level']}"), body_style))
        if audience:
            story.append(Paragraph(escape_xml(f"Audience: {audience}"), body_style))
        story.append(Paragraph(escape_xml(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"), body_style))
        story.append(Spacer(1, 24))
        
        for line in content.split('\n'):
            line = line.strip()
            if not line:
                story.append(Spacer(1, 6))
                continue
            safe_line = md_to_reportlab(line)
            if line.startswith('#'):
                text = line.lstrip('#').strip()
                if text: story.append(Paragraph(md_to_reportlab(text), styles['NotesHeading']))
            elif line.startswith('```'):
                code_text = line.replace('```', '').strip()
                if code_text: story.append(Paragraph(escape_xml(code_text), styles['CustomCode']))
            else:
                story.append(Paragraph(safe_line, body_style))
        
        doc.build(story)
        return str(output_path)
