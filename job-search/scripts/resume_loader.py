import os
import tempfile
from pathlib import Path

import pdfplumber
from docx import Document


def _read_text_file(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_pdf(path: Path) -> str:
    chunks = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks).strip()


def _read_docx(path: Path) -> str:
    doc = Document(str(path))
    return "\n".join([p.text for p in doc.paragraphs]).strip()


def _read_doc(path: Path) -> str:
    try:
        import win32com.client  # type: ignore
    except Exception as ex:
        raise RuntimeError(
            "读取 .doc 需要 pywin32 且本机已安装 Word，请先安装 pywin32 或将文件另存为 .docx。"
        ) from ex

    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    doc = None
    tmp_path = None
    try:
        doc = word.Documents.Open(str(path.resolve()))
        fd, tmp_name = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        tmp_path = Path(tmp_name)
        doc.SaveAs(str(tmp_path), FileFormat=2)
    finally:
        if doc is not None:
            doc.Close(False)
        word.Quit()

    if not tmp_path:
        return ""
    try:
        return _read_text_file(tmp_path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def load_resume_text(resume_path: str) -> str:
    path = Path(resume_path)
    if not path.exists():
        raise FileNotFoundError(f"简历文件不存在: {resume_path}")

    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        text = _read_text_file(path)
    elif suffix == ".pdf":
        text = _read_pdf(path)
    elif suffix == ".docx":
        text = _read_docx(path)
    elif suffix == ".doc":
        text = _read_doc(path)
    else:
        raise ValueError("仅支持 pdf/md/doc/docx/txt 文件。")

    clean = (text or "").strip()
    if not clean:
        raise ValueError(
            f"简历内容为空，无法继续。文件: {path.resolve()}，类型: {suffix or '无后缀'}。"
            "若是扫描版 PDF，请先转为可复制文本或 md/txt。"
        )
    return clean
