import os
import re
import subprocess
from pathlib import Path
from flask import Flask, request, render_template, send_file, jsonify

app = Flask(__name__)

PROJECTS = Path("/app/projects")
OUTPUT = Path("/app/output")
IMAGES = PROJECTS / "images"
PROJECTS.mkdir(exist_ok=True)
OUTPUT.mkdir(exist_ok=True)
IMAGES.mkdir(exist_ok=True)

DEFAULT_TYPST = """\
#set page(width: 10cm, height: auto)
#set text(
  font: ("Noto Serif CJK SC", "Noto Serif"),
  size: 10.5pt,
  lang: "zh",
)

= 标题

这是一个新的 Typst 文档。

== 二级标题

在这里开始编写内容。
"""

if not any(PROJECTS.glob("*.typ")):
    (PROJECTS / "welcome.typ").write_text(DEFAULT_TYPST, encoding="utf-8")


def _typst_env():
    env = {**os.environ}
    env["TYPST_FONT_PATHS"] = os.environ.get("TYPST_FONT_PATHS", "/usr/share/fonts")
    return env


def _resolve_includes(filepath, depth=0, seen=None):
    if depth > 10:
        return ""
    if seen is None:
        seen = set()
    resolved = filepath.resolve()
    if resolved in seen:
        return f"// [skip] 循环引用: {resolved.name}"
    seen.add(resolved)
    if not filepath.is_file():
        return f"// [error] 文件不存在: {filepath.name}"
    base_dir = filepath.parent
    lines = []
    for line in filepath.read_text("utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#include"):
            m = re.match(r'#include\s+"([^"]+)"', stripped)
            if m:
                lines.append(_resolve_includes(base_dir / m.group(1), depth + 1, seen))
                continue
        if stripped.startswith("#import"):
            m = re.match(r'#import\s+"([^"]+)"', stripped)
            if m:
                lines.append(_resolve_includes(base_dir / m.group(1), depth + 1, seen))
                continue
        lines.append(line)
    return "\n".join(lines)


def _extract_meta(content):
    """
    从 #set document(...) 中提取 title 和 author。
    回退: title → 第一个 = 标题; author → 空字符串。
    """
    title, author = "", ""
    m = re.search(r'#set\s+document$$(.+?)$$', content, re.DOTALL)
    if m:
        block = m.group(1)
        tm = re.search(r'title:\s*"([^"]*)"', block)
        if tm:
            title = tm.group(1)
        am = re.search(r'author:\s*"([^"]*)"', block)
        if not am:
            am = re.search(r'author:\s*$$([^)]*)$$', block)
        if am:
            author = '、'.join(re.findall(r'"([^"]*)"', am.group(1)))
    if not title:
        hm = re.search(r'^=\s+(.+)$', content, re.MULTILINE)
        if hm:
            title = hm.group(1).strip()
    return title, author


def _extract_cover_image(content, base_dir):
    IMG_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
    for match in re.findall(r'image\(\s*"([^"]+)"', content):
        img_path = (base_dir / match).resolve()
        if img_path.is_file() and img_path.suffix.lower() in IMG_EXTS:
            return img_path
    return None


def _strip_typst_directives(content):
    out, paren_depth = [], 0
    for line in content.splitlines():
        stripped = line.strip()
        if paren_depth > 0:
            paren_depth += stripped.count('(') - stripped.count(')')
            if paren_depth <= 0:
                paren_depth = 0
            continue
        if re.match(r'^#set\b', stripped):
            d = stripped.count('(') - stripped.count(')')
            if d > 0:
                paren_depth = d
            continue
        out.append(line)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip()


def _sanitize_filename(name):
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', '_', name)
    return name.strip('. ')[:200] or 'untitled'


# ── Page ───────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Image API (serve) ─────────────────────────────────

@app.route("/images/<path:filename>")
def serve_images(filename):
    fp = IMAGES / filename
    if not fp.is_file():
        return jsonify({"error": "Not found"}), 404
    return send_file(fp)


# ── File API ──────────────────────────────────────────

@app.route("/api/files")
def list_files():
    return jsonify([
        {"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime}
        for f in sorted(PROJECTS.glob("*.typ"))
    ])


@app.route("/api/file/<path:filename>", methods=["GET"])
def get_file(filename):
    fp = PROJECTS / Path(filename).name
    if not fp.is_file():
        return jsonify({"error": "文件不存在"}), 404
    return jsonify({"content": fp.read_text("utf-8")})


@app.route("/api/file/<path:filename>", methods=["POST"])
def save_file(filename):
    name = Path(filename).name
    if not name.endswith(".typ"):
        name += ".typ"
    (PROJECTS / name).write_text(request.json.get("content", ""), "utf-8")
    return jsonify({"success": True, "filename": name})


@app.route("/api/file/<path:filename>", methods=["DELETE"])
def delete_file(filename):
    fp = PROJECTS / Path(filename).name
    if fp.is_file():
        fp.unlink()
    return jsonify({"success": True})


@app.route("/api/create", methods=["POST"])
def create_file():
    name = request.json.get("filename", "untitled.typ")
    if not name.endswith(".typ"):
        name += ".typ"
    fp = PROJECTS / name
    if fp.exists():
        return jsonify({"error": "文件已存在"}), 409
    fp.write_text(DEFAULT_TYPST, "utf-8")
    return jsonify({"success": True, "filename": name})


@app.route("/api/upload", methods=["POST"])
def upload_file():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "请选择文件"}), 400
    name = Path(file.filename).name
    if not name.endswith(".typ"):
        return jsonify({"error": "仅支持 .typ 文件"}), 400
    (PROJECTS / name).write_text(file.read().decode("utf-8"), "utf-8")
    return jsonify({"success": True, "filename": name})


# ── Image API (manage) ────────────────────────────────

ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".svg", ".pdf", ".gif", ".webp"}


@app.route("/api/images")
def list_images():
    return jsonify([
        {"name": f.name, "size": f.stat().st_size}
        for f in sorted(IMAGES.glob("*")) if f.suffix.lower() in ALLOWED_IMG
    ])


@app.route("/api/upload-image", methods=["POST"])
def upload_image():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "请选择文件"}), 400
    name = Path(file.filename).name
    if Path(name).suffix.lower() not in ALLOWED_IMG:
        return jsonify({"error": "不支持的图片格式"}), 400
    file.save(IMAGES / name)
    return jsonify({"success": True, "filename": name, "path": f"images/{name}"})


@app.route("/api/image/<path:filename>", methods=["DELETE"])
def delete_image(filename):
    fp = IMAGES / Path(filename).name
    if fp.is_file():
        fp.unlink()
    return jsonify({"success": True})


# ── Preview (HTML) ────────────────────────────────────

@app.route("/api/preview", methods=["POST"])
def preview():
    filename = request.json.get("filename", "")
    fp = PROJECTS / filename
    if not fp.is_file():
        return jsonify({"error": "文件不存在"}), 404
    content = _resolve_includes(fp)
    result = subprocess.run(
        ["pandoc", "-f", "typst", "-t", "html5",
         "--wrap=none", "--syntax-highlighting=tango",
         f"--resource-path={fp.parent}"],
        input=content, capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return jsonify({"error": result.stderr.strip()})
    return result.stdout, 200, {"Content-Type": "text/html; charset=utf-8"}


# ── Preview (PDF) ─────────────────────────────────────

@app.route("/api/preview-pdf", methods=["POST"])
def preview_pdf():
    filename = request.json.get("filename", "")
    fp = PROJECTS / filename
    if not fp.is_file():
        return jsonify({"error": "文件不存在"}), 404
    stem = Path(filename).stem
    out_path = OUTPUT / f"{stem}_preview.pdf"
    result = subprocess.run(
        ["typst", "compile", str(fp), str(out_path)],
        capture_output=True, text=True, timeout=30, env=_typst_env(),
    )
    if result.returncode != 0:
        return jsonify({"error": result.stderr.strip()})
    return jsonify({"success": True, "url": f"/preview-dl/{stem}_preview.pdf"})


@app.route("/preview-dl/<path:filename>")
def serve_preview(filename):
    fp = OUTPUT / filename
    if not fp.is_file():
        return jsonify({"error": "Not found"}), 404
    return send_file(fp, mimetype="application/pdf")


# ── Export ────────────────────────────────────────────

EXPORT_CONFIG = {
    "pdf":   {"ext": "pdf",   "mime": "application/pdf"},
    "epub":  {"ext": "epub",  "mime": "application/epub+zip"},
    "html":  {"ext": "html",  "mime": "text/html; charset=utf-8"},
    "docx":  {"ext": "docx",  "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "md":    {"ext": "md",    "mime": "text/markdown; charset=utf-8"},
    "latex": {"ext": "tex",   "mime": "application/x-tex; charset=utf-8"},
}


@app.route("/api/export", methods=["POST"])
def export():
    filename = request.json.get("filename", "")
    target = request.json.get("target", "")
    fp = PROJECTS / filename
    if not fp.is_file():
        return jsonify({"error": "文件不存在"}), 404
    if target not in EXPORT_CONFIG:
        return jsonify({"error": f"不支持: {target}"}), 400

    cfg = EXPORT_CONFIG[target]
    raw = fp.read_text("utf-8")
    meta_title, meta_author = _extract_meta(raw)
    meta_title = meta_title or Path(filename).stem
    safe_name = _sanitize_filename(meta_title)
    out_path = OUTPUT / f"{safe_name}.{cfg['ext']}"

    # ── PDF ──
    if target == "pdf":
        result = subprocess.run(
            ["typst", "compile", str(fp), str(out_path)],
            capture_output=True, text=True, timeout=30, env=_typst_env(),
        )

    # ── EPUB ──
    elif target == "epub":
        cover_img = _extract_cover_image(raw, fp.parent)
        clean = _strip_typst_directives(_resolve_includes(fp))

        epub_css = OUTPUT / "_epub_style.css"
        epub_css.write_text(
            "body{margin:1em}\n"
            "h1{page-break-before:auto!important;page-break-after:avoid}\n"
            "h2,h3,h4{page-break-before:auto}\n"
            "section{page-break-before:auto!important}\n",
            encoding="utf-8",
        )

        args = [
            "pandoc", "-f", "typst", "-o", str(out_path),
            "--toc",
            "--metadata", "toc-title=目录",
            "--metadata", f"title={meta_title}",
            "--epub-title-page=false",
            "--css", str(epub_css),
            f"--resource-path={fp.parent}",
        ]
        if meta_author:
            args += ["--metadata", f"creator={meta_author}"]
        if cover_img:
            args += ["--epub-cover-image", str(cover_img)]

        result = subprocess.run(
            args, input=clean, capture_output=True, text=True, timeout=30,
        )

    # ── 其他格式 ──
    else:
        args = ["pandoc", str(fp), "-o", str(out_path)]
        if target == "html":
            args += ["--syntax-highlighting=tango", "--metadata", f"title={meta_title}"]
        elif target == "md":
            args += ["--wrap=none"]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        return jsonify({"success": False, "error": result.stderr.strip()})

    return jsonify({
        "success": True,
        "url": f"/dl/{safe_name}.{cfg['ext']}",
        "filename": f"{safe_name}.{cfg['ext']}",
    })


# ── Serve download ────────────────────────────────────

@app.route("/dl/<path:filename>")
def serve_output(filename):
    fp = OUTPUT / filename
    if not fp.is_file():
        return jsonify({"error": "Not found"}), 404
    mime_map = {
        ".pdf": "application/pdf",
        ".epub": "application/epub+zip",
        ".html": "text/html; charset=utf-8",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown; charset=utf-8",
        ".tex": "application/x-tex; charset=utf-8",
    }
    return send_file(fp, mimetype=mime_map.get(fp.suffix.lower(), "application/octet-stream"),
                     as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
