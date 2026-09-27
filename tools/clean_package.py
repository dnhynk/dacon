"""Write a clean copy of a submission zip: every Python module loses its comments and docstrings, other files are copied
unchanged. Modules are parsed and re-emitted with ast.unparse; each cleaned module must parse back to the same AST as the
original without docstrings, so the clean package runs exactly as the original.

    python tools/clean_package.py <in submit.zip> <out submit.zip>
"""
import ast
import hashlib
import sys
import zipfile
from pathlib import Path

DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def without_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, DOC_OWNERS) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                node.body = node.body[1:] or [ast.Pass()]
    return tree


def clean(src, name):
    out = ast.unparse(without_docstrings(ast.parse(src))) + '\n'
    if ast.dump(ast.parse(out)) != ast.dump(without_docstrings(ast.parse(src))):
        raise SystemExit(f'{name}: the cleaned module does not parse to the same AST')
    compile(out, name, 'exec')
    return out


def main(src_zip, dst_zip):
    dst = Path(dst_zip)
    if dst.exists():
        raise SystemExit(f'refusing to overwrite {dst}')
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src_zip) as zin, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.endswith('.py'):
                data = clean(data.decode('utf-8'), info.filename).encode('utf-8')
            zout.writestr(info.filename, data)
    print(dst, hashlib.sha256(dst.read_bytes()).hexdigest())


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
