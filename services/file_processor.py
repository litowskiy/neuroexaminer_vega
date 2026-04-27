import hashlib
import io
import re
from pathlib import Path


def extract_text(content: bytes, filename: str) -> str:
    """Extract plain text from PDF, DOCX, TXT or MD file content."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        text = _from_pdf(content)
    elif suffix == ".docx":
        text = _from_docx(content)
    elif suffix in (".txt", ".md"):
        text = _from_txt(content)
    else:
        raise ValueError(f"Unsupported format: {suffix}. Use PDF, DOCX, TXT or MD.")

    # Normalize whitespace
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compute_hash(text: str) -> str:
    """SHA-256 хэш текста — используется для дедупликации файлов."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _from_pdf(content: bytes) -> str:
    import pypdf  # type: ignore

    reader = pypdf.PdfReader(io.BytesIO(content))
    pages = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            pages.append(page_text.strip())
    return "\n\n".join(pages)


def _from_docx(content: bytes) -> str:
    import docx  # type: ignore

    doc = docx.Document(io.BytesIO(content))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _from_txt(content: bytes) -> str:
    for encoding in ("utf-8", "cp1251", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")
