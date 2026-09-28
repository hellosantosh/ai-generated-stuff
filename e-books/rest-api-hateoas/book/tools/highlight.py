"""
Syntax highlighting for the book's code: Java, YAML, XML, JSON, HTTP exchanges and shell.

Each highlighter turns source text into a list of HTML lines. A highlight span never
crosses a line break, so callers can number, trim or paginate the lines freely.

The scanners are small and deliberately conservative. They only need to know whether a
character is inside a comment, a string or a text block, so that "//" inside a URL string
is never taken for a comment.
"""
import html
import re

JAVA_KEYWORDS = set("""
abstract assert boolean break byte case catch char class const continue default do double
else enum extends final finally float for goto if implements import instanceof int interface
long native new package private protected public record return sealed permits short static
strictfp super switch synchronized this throw throws transient try var void volatile while
yield true false null non-sealed
""".split())


def _spans(tokens):
    """[(css_class or None, text)] -> list of HTML lines."""
    lines, current = [], []
    for cls, text in tokens:
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if i:
                lines.append("".join(current))
                current = []
            if part:
                esc = html.escape(part, quote=False)
                current.append(f'<span class="{cls}">{esc}</span>' if cls else esc)
    lines.append("".join(current))
    return lines


# ----------------------------------------------------------------------------- Java

def java(src):
    tokens, i, n = [], 0, len(src)
    buf = []

    def flush():
        if buf:
            tokens.extend(_java_words("".join(buf)))
            buf.clear()

    while i < n:
        two = src[i:i + 2]
        if two == "//":
            flush()
            j = src.find("\n", i)
            j = n if j == -1 else j
            tokens.append(("cm", src[i:j]))
            i = j
        elif two == "/*":
            flush()
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            tokens.append(("cm", src[i:j]))
            i = j
        elif src.startswith('"""', i):
            flush()
            j = src.find('"""', i + 3)
            j = n if j == -1 else j + 3
            tokens.append(("st", src[i:j]))
            i = j
        elif src[i] in "\"'":
            flush()
            quote, j = src[i], i + 1
            while j < n and src[j] != quote and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            tokens.append(("st", src[i:j]))
            i = j
        else:
            buf.append(src[i])
            i += 1
    flush()
    return _spans(tokens)


def _java_words(code):
    out = []
    for m in re.finditer(r"@[A-Za-z_][\w.]*|[A-Za-z_]\w*|\d[\d_]*(?:\.\d+)?[LlDdFf]?|[^A-Za-z_\d@]+|.", code):
        word = m.group(0)
        if word.startswith("@") and word != "@interface":
            out.append(("an", word))
        elif word in JAVA_KEYWORDS:
            out.append(("kw", word))
        elif word[0].isdigit():
            out.append(("nu", word))
        else:
            out.append((None, word))
    return out


# ----------------------------------------------------------------------------- YAML

def yaml(src):
    tokens = []
    for k, line in enumerate(src.split("\n")):
        if k:
            tokens.append((None, "\n"))
        tokens.extend(_yaml_line(line))
    return _spans(tokens)


def _yaml_line(line):
    out = []
    comment = _yaml_comment_start(line)
    body, rest = (line, "") if comment < 0 else (line[:comment], line[comment:])
    m = re.match(r"^(\s*(?:-\s+)?)([^\s:#\"'{}\[\],][^:#{}\[\],]*?|\"[^\"]*\"|'[^']*')(:)(?=\s|$)", body)
    if m:
        out.append((None, m.group(1)))
        out.append(("key", m.group(2)))
        out.append((None, m.group(3)))
        body = body[m.end():]
    for part in re.split(r"(\"(?:[^\"\\]|\\.)*\"|'[^']*')", body):
        if part:
            out.append(("st" if part[0] in "\"'" else None, part))
    if rest:
        out.append(("cm", rest))
    return out


def _yaml_comment_start(line):
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return i
    return -1


# ----------------------------------------------------------------------------- XML

def xml(src):
    tokens, pos = [], 0
    for m in re.finditer(r"<!--.*?-->|<\?.*?\?>|</?[\w:.-]+|/?>|[\w:.-]+(?==)|\"[^\"]*\"", src, re.S):
        if m.start() > pos:
            tokens.append((None, src[pos:m.start()]))
        text = m.group(0)
        if text.startswith("<!--") or text.startswith("<?"):
            tokens.append(("cm", text))
        elif text.startswith("<") or text in (">", "/>"):
            tokens.append(("tg", text))
        elif text.startswith('"'):
            tokens.append(("st", text))
        else:
            tokens.append(("at", text))
        pos = m.end()
    tokens.append((None, src[pos:]))
    return _spans(tokens)


# ----------------------------------------------------------------------------- JSON

def json_text(src):
    tokens, pos = [], 0
    for m in re.finditer(r'"(?:[^"\\]|\\.)*"(\s*:)?|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\btrue\b|\bfalse\b|\bnull\b'
                         r'|/\*.*?\*/', src):
        if m.start() > pos:
            tokens.append((None, src[pos:m.start()]))
        text = m.group(0)
        if text.startswith("/*"):
            tokens.append(("cm", text))
        elif text.startswith('"'):
            if m.group(1):
                tokens.append(("key", text[:len(text) - len(m.group(1))]))
                tokens.append((None, m.group(1)))
            else:
                tokens.append(("st", text))
        else:
            tokens.append(("nu", text))
        pos = m.end()
    tokens.append((None, src[pos:]))
    return _spans(tokens)


# ----------------------------------------------------------------------------- HTTP

REQUEST_LINE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S+ HTTP/\d(?:\.\d)?$")
STATUS_LINE = re.compile(r"^HTTP/\d(?:\.\d)? (\d{3})\b.*$")


def http(src):
    """An HTTP exchange: request line, headers, body, then the same for the response."""
    out, lines, i = [], src.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if REQUEST_LINE.match(line) or STATUS_LINE.match(line):
            status = STATUS_LINE.match(line)
            cls = "rl" if not status else ("ok" if status.group(1)[0] in "123" else "er")
            gap = '<span class="gap"></span>' if out else ""
            out.append(f'{gap}<span class="{cls}">{html.escape(line, quote=False)}</span>')
            i += 1
            while i < len(lines) and lines[i].strip():          # headers
                name, _, value = lines[i].partition(":")
                out.append(f'<span class="hd">{html.escape(name)}</span>:'
                           f'{html.escape(value, quote=False)}')
                i += 1
            body = []
            i += 1                                              # the blank line
            while i < len(lines) and not (STATUS_LINE.match(lines[i]) or REQUEST_LINE.match(lines[i])):
                body.append(lines[i])
                i += 1
            while body and not body[-1].strip():
                body.pop()
            if body:
                out.append("")
                text = "\n".join(body)
                if text.lstrip()[:1] in "{[":
                    out.extend(json_text(text))
                elif re.match(r"^(id|event|data|retry):", text):
                    out.extend(sse(text))
                elif "=" in text and "&" in text and " " not in text:
                    out.extend(_spans([("st", text)]))
                else:
                    out.extend(_spans([(None, text)]))
        else:
            out.append(html.escape(line, quote=False))
            i += 1
    return out


def sse(src):
    out = []
    for line in src.split("\n"):
        field, sep, value = line.partition(":")
        if sep and field in ("id", "event", "data", "retry"):
            rest = json_text(value)[0] if value.strip().startswith("{") else html.escape(value)
            out.append(f'<span class="key">{field}</span>:{rest}')
        else:
            out.append(html.escape(line))
    return out


# ----------------------------------------------------------------------------- shell

def shell(src):
    out = []
    for line in src.split("\n"):
        if line.lstrip().startswith("#"):
            out.append(f'<span class="cm">{html.escape(line)}</span>')
            continue
        m = re.match(r"^(\$ )(.*)$", line)
        prompt, rest = (m.group(1), m.group(2)) if m else ("", line)
        pieces = _spans([("st" if p[:1] in "\"'" else None, p)
                         for p in re.split(r"(\"[^\"]*\"|'[^']*')", rest) if p])[0]
        out.append((f'<span class="pr">{prompt}</span>' if prompt else "") + pieces)
    return out


def plain(src):
    return _spans([(None, src)])


BY_EXTENSION = {".java": java, ".yaml": yaml, ".yml": yaml, ".xml": xml, ".json": json_text,
                ".http": http, ".sh": shell}


def for_path(path):
    for ext, fn in BY_EXTENSION.items():
        if str(path).endswith(ext):
            return fn
    return plain


def by_name(name):
    return {"java": java, "yaml": yaml, "xml": xml, "json": json_text, "http": http,
            "shell": shell, "sh": shell, "text": plain, "sse": sse}[name]


if __name__ == "__main__":   # a quick visual check: python3 highlight.py File.java
    import sys
    path = sys.argv[1]
    for line in for_path(path)(open(path).read())[:40]:
        print(line)
