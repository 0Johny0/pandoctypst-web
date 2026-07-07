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
    env["TYPST_FONT_PATHS"] = os.environ.get(
        "TYPST_FONT_PATHS", "/usr/share/fonts"
    )
    return env


def _resolve_includes(filepath, depth=0, seen=None):
    """递归展开 Typst 的 #include 和 #import"""
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

        # #include "filename.typ"
        if stripped.startswith("#include"):
            m = re.match(r'#include\s+"([^"]+)"', stripped)
            if m:
                inc_path = base_dir / m.group(1)
                lines.append(_resolve_includes(inc_path, depth + 1, seen))
                continue

        # #import "filename.typ": *
        if stripped.startswith("#import"):
            m = re.match(r'#import\s+"([^"]+)"', stripped)
            if m:
                imp_path = base_dir / m.group(1)
                lines.append(_resolve_includes(imp_path, depth + 1, seen))
                continue

        lines.append(line)

    return "\n".join(lines)


# ── Page ───────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── File API ───────────────────────────────────────────

@app.route("/api/files")
def list_files():
    files = []
    for f in sorted(PROJECTS.glob("*.typ")):
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
    if not name.endswith(".typ"):
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
    content = file.read().decode("utf-8")
    (PROJECTS / name).write_text(content, "utf-8")
    return jsonify({"success": True, "filename": name})


# ── Image API ──────────────────────────────────────────

ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".svg", ".pdf", ".gif", ".webp"}


@app.route("/api/images")
def list_images():
    files = []
    for f in sorted(IMAGES.glob("*")):
        if f.suffix.lower() in ALLOWED_IMG:
            files.append({"name": f.name, "size": f.stat().st_size})
    return jsonify(files)


@app.route("/api/upload-image", methods=["POST"])
def upload_image():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "请选择文件"}), 400
    name = Path(file.filename).name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_IMG:
        return jsonify({"error": "不支持的图片格式"}), 400
    file.save(IMAGES / name)
    return jsonify({"success": True, "filename": name, "path": f"images/{name}"})


@app.route("/api/image/<path:filename>", methods=["DELETE"])
def delete_image(filename):
    fp = IMAGES / Path(filename).name
    if fp.is_file():
        fp.unlink()
    return jsonify({"success": True})


# ── Preview (pandoc typst → HTML) ─────────────────────

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
        input=content,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return jsonify({"error": result.stderr.strip()})

    return result.stdout, 200, {"Content-Type": "text/html; charset=utf-8"}


# ── Export ─────────────────────────────────────────────

EXPORT_CONFIG = {
    "pdf":   {"ext": "pdf",  "mime": "application/pdf"},
    "epub":  {"ext": "epub", "mime": "application/epub+zip"},
    "html":  {"ext": "html", "mime": "text/html; charset=utf-8"},
    "docx":  {"ext": "docx", "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "md":    {"ext": "md",   "mime": "text/markdown; charset=utf-8"},
    "latex": {"ext": "tex",  "mime": "application/x-tex; charset=utf-8"},
}


@app.route("/api/export", methods=["POST"])
def export():
    filename = request.json.get("filename", "")
    target = request.json.get("target", "")
    title = request.json.get("title", "")

    fp = PROJECTS / filename
    if not fp.is_file():
        return jsonify({"error": "文件不存在"}), 404
    if target not in EXPORT_CONFIG:
        return jsonify({"error": f"不支持: {target}"}), 400

    stem = Path(filename).stem
    cfg = EXPORT_CONFIG[target]
    out_path = OUTPUT / f"{stem}.{cfg['ext']}"
    doc_title = title or stem

    if target == "pdf":
        result = subprocess.run(
            ["typst", "compile", str(fp), str(out_path)],
            capture_output=True, text=True, timeout=30,
            env=_typst_env(),
        )
    else:
        pandoc_args = ["pandoc", str(fp), "-o", str(out_path)]
        if target == "epub":
            pandoc_args += ["--toc", "--metadata", f"title={doc_title}"]
        elif target == "html":
            pandoc_args += ["--syntax-highlighting=tango",
                            "--metadata", f"title={doc_title}"]
        elif target == "md":
            pandoc_args += ["--wrap=none"]
        result = subprocess.run(
            pandoc_args,
            capture_output=True, text=True, timeout=30,
        )

    if result.returncode != 0:
        return jsonify({"success": False, "error": result.stderr.strip()})

    return jsonify({
        "success": True,
        "url": f"/dl/{stem}.{cfg['ext']}",
        "filename": f"{stem}.{cfg['ext']}",
    })


# ── Serve download ─────────────────────────────────────

@app.route("/dl/<path:filename>")
def serve_output(filename):
    fp = OUTPUT / filename
    if not fp.is_file():
        return jsonify({"error": "Not found"}), 404
    ext = fp.suffix.lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".epub": "application/epub+zip",
        ".html": "text/html; charset=utf-8",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown; charset=utf-8",
        ".tex": "application/x-tex; charset=utf-8",
    }
    mime = mime_map.get(ext, "application/octet-stream")
    return send_file(fp, mimetype=mime, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
