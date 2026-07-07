import os
import subprocess
import uuid
from pathlib import Path
from flask import Flask, request, render_template, send_file, jsonify

app = Flask(__name__)

PROJECTS = Path("/app/projects")
OUTPUT = Path("/app/output")
UPLOAD = Path("/app/uploads")
PROJECTS.mkdir(exist_ok=True)
OUTPUT.mkdir(exist_ok=True)
UPLOAD.mkdir(exist_ok=True)

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

DEFAULT_MD = """\
# 标题

这是一个新的 Markdown 文档。

## 二级标题

在这里开始编写内容。
"""

if not any(PROJECTS.glob("*.typ")) and not any(PROJECTS.glob("*.md")):
    (PROJECTS / "welcome.typ").write_text(DEFAULT_TYPST, encoding="utf-8")
    (PROJECTS / "welcome.md").write_text(DEFAULT_MD, encoding="utf-8")


# ── Pages ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/editor")
def editor_page():
    return render_template("editor.html")


# ── File API ───────────────────────────────────────────

@app.route("/api/files")
def list_files():
    files = []
    for ext in ("*.typ", "*.md"):
        for f in sorted(PROJECTS.glob(ext)):
            s = f.stat()
            files.append({
                "name": f.name,
                "size": s.st_size,
                "modified": s.st_mtime,
            })
    return jsonify(files)


@app.route("/api/file/<path:filename>", methods=["GET"])
def get_file(filename):
    fp = PROJECTS / Path(filename).name
    if not fp.is_file():
        return jsonify({"error": "文件不存在"}), 404
    return jsonify({"content": fp.read_text("utf-8")})


@app.route("/api/file/<path:filename>", methods=["POST"])
def save_file(filename):
    name = Path(filename).name
    if not name.endswith((".typ", ".md")):
        name += ".typ"
    content = request.json.get("content", "")
    (PROJECTS / name).write_text(content, "utf-8")
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
    if not name.endswith((".typ", ".md")):
        name += ".typ"
    fp = PROJECTS / name
    if fp.exists():
        return jsonify({"error": "文件已存在"}), 409
    content = DEFAULT_TYPST if name.endswith(".typ") else DEFAULT_MD
    fp.write_text(content, "utf-8")
    return jsonify({"success": True, "filename": name})


# ── Preview API ────────────────────────────────────────

@app.route("/api/preview/md", methods=["POST"])
def preview_md():
    """Markdown → HTML (instant preview)"""
    content = request.json.get("content", "")
    result = subprocess.run(
        [
            "pandoc", "-f", "markdown+smart", "-t", "html5",
            "--wrap=none", "--highlight-style=tango",
        ],
        input=content,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return result.stderr, 500
    return result.stdout, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/preview/typst", methods=["POST"])
def preview_typst():
    """Typst → HTML (compile preview)"""
    filename = request.json.get("filename", "")
    fp = PROJECTS / filename
    if not fp.is_file():
        return jsonify({"error": "文件不存在"}), 404

    tmp_html = OUTPUT / f"_preview_{Path(filename).stem}.html"
    env = _typst_env()

    result = subprocess.run(
        ["typst", "compile", str(fp), str(tmp_html), "--format", "html"],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    if result.returncode != 0:
        return jsonify({"error": result.stderr.strip()})

    html = tmp_html.read_text("utf-8") if tmp_html.exists() else ""
    tmp_html.unlink(missing_ok=True)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


# ── Compile API ────────────────────────────────────────

@app.route("/api/compile", methods=["POST"])
def compile_pdf():
    """Compile Typst → PDF"""
    filename = request.json.get("filename", "")
    fp = PROJECTS / filename
    if not fp.is_file():
        return jsonify({"error": "文件不存在"}), 404

    stem = Path(filename).stem
    pdf_path = OUTPUT / f"{stem}.pdf"
    env = _typst_env()

    result = subprocess.run(
        ["typst", "compile", str(fp), str(pdf_path)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    if result.returncode != 0:
        return jsonify({"success": False, "error": result.stderr.strip()})

    ts = os.path.getmtime(pdf_path)
    return jsonify({"success": True, "pdf": f"/dl/{stem}.pdf?t={ts}"})


@app.route("/api/compile/epub", methods=["POST"])
def compile_epub():
    """Compile to EPUB"""
    filename = request.json.get("filename", "")
    fp = PROJECTS / filename
    if not fp.is_file():
        return jsonify({"error": "文件不存在"}), 404

    stem = Path(filename).stem
    epub_path = OUTPUT / f"{stem}.epub"
    title = request.json.get("title", stem)

    if filename.endswith(".typ"):
        # typ → html → epub
        tmp_html = OUTPUT / f"_epub_{stem}.html"
        env = _typst_env()
        r1 = subprocess.run(
            ["typst", "compile", str(fp), str(tmp_html), "--format", "html"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if r1.returncode != 0:
            return jsonify({"success": False, "error": r1.stderr.strip()})
        r2 = subprocess.run(
            [
                "pandoc", str(tmp_html), "-o", str(epub_path),
                "--toc", "--metadata", f"title={title}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        tmp_html.unlink(missing_ok=True)
    else:
        # md / other → epub
        r2 = subprocess.run(
            [
                "pandoc", str(fp), "-o", str(epub_path),
                "--toc", "--metadata", f"title={title}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    if r2.returncode != 0:
        return jsonify({"success": False, "error": r2.stderr.strip()})

    return jsonify({"success": True, "epub": f"/dl/{stem}.epub"})


# ── Converter (upload) ─────────────────────────────────

CONVERTERS = {
    "pdf": lambda i, o: (
        ["typst", "compile", str(i), str(o)] if i.suffix == ".typ"
        else ["pandoc", str(i), "-o", str(o)]
    ),
    "html": lambda i, o: ["pandoc", str(i), "-o", str(o), "-s"],
    "docx": lambda i, o: ["pandoc", str(i), "-o", str(o)],
    "epub": lambda i, o: ["pandoc", str(i), "-o", str(o), "--toc"],
    "md":   lambda i, o: ["pandoc", str(i), "-o", str(o), "--wrap=none"],
    "tex":  lambda i, o: ["pandoc", str(i), "-o", str(o)],
}

TYPST_ONLY = {".typ"}


@app.route("/convert", methods=["POST"])
def convert():
    file = request.files.get("file")
    target = request.form.get("target", "")
    if not file or not file.filename:
        return jsonify({"error": "请选择文件"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext in TYPST_ONLY and target != "pdf":
        return jsonify({"error": "Typst 文件只能转换为 PDF"}), 400
    if target not in CONVERTERS:
        return jsonify({"error": f"不支持: {target}"}), 400

    uid = uuid.uuid4().hex[:8]
    src = UPLOAD / f"{uid}_{file.filename}"
    file.save(src)

    stem = Path(file.filename).stem
    out = OUTPUT / f"{stem}.{target}"

    try:
        cmd = CONVERTERS[target](src, out)
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return jsonify({"error": r.stderr or "转换失败"}), 500
        return send_file(out, as_attachment=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        src.unlink(missing_ok=True)


# ── Serve output files ─────────────────────────────────

@app.route("/dl/<path:filename>")
def serve_output(filename):
    fp = OUTPUT / filename
    if not fp.is_file():
        return jsonify({"error": "Not found"}), 404
    ext = fp.suffix.lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".epub": "application/epub+zip",
    }
    mime = mime_map.get(ext, "application/octet-stream")
    return send_file(fp, mimetype=mime, as_attachment=True)


# ── Helpers ─────────────────────────────────────────────

def _typst_env():
    env = {**os.environ}
    env["TYPST_FONT_PATHS"] = os.environ.get(
        "TYPST_FONT_PATHS", "/usr/share/fonts"
    )
    return env


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
