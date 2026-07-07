import os
import subprocess
from pathlib import Path
from flask import Flask, request, render_template, send_file, jsonify

app = Flask(__name__)

PROJECTS = Path("/app/projects")
OUTPUT = Path("/app/output")
PROJECTS.mkdir(exist_ok=True)
OUTPUT.mkdir(exist_ok=True)

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


# ── Preview (pandoc typst → HTML, 实时) ───────────────

@app.route("/api/preview", methods=["POST"])
def preview():
    filename = request.json.get("filename", "")
    fp = PROJECTS / filename
    if not fp.is_file():
        return jsonify({"error": "文件不存在"}), 404

    content = fp.read_text("utf-8")
    result = subprocess.run(
        ["pandoc", "-f", "typst", "-t", "html5",
         "--wrap=none", "--highlight-style=tango"],
        input=content,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return jsonify({"error": result.stderr.strip()})

    return result.stdout, 200, {"Content-Type": "text/html; charset=utf-8"}


# ── Export (编译下载) ──────────────────────────────────

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
        # typst compile → PDF（最高质量）
        result = subprocess.run(
            ["typst", "compile", str(fp), str(out_path)],
            capture_output=True, text=True, timeout=30,
            env=_typst_env(),
        )
    else:
        # pandoc -f typst → 其它格式
        pandoc_args = ["pandoc", str(fp), "-o", str(out_path)]
        if target == "epub":
            pandoc_args += ["--toc", "--metadata", f"title={doc_title}"]
        elif target == "html":
            pandoc_args += ["-s", "--highlight-style=tango",
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
