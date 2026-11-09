"""
publicar.py - Monta o site estático do painel (GitHub Pages) a partir de web/painel.html e do resultado do cálculo.

Uso: python src/publicar.py [painel.json] [pasta_site]
Padrão: data/painel.json -> _site/index.html, _site/painel.json, _site/manual.html e _site/docs/*.xlsx
Dependências: biblioteca padrão; openpyxl para as planilhas (sem ele, o site sai sem a pasta docs).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
MODELO = RAIZ / "web" / "painel.html"
CABECALHO = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
body{margin:0;font:14px/1.5 system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>
"""


def main():
    painel = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "data" / "painel.json"
    site = Path(sys.argv[2]) if len(sys.argv) > 2 else RAIZ / "_site"
    if not painel.exists():
        sys.exit(f"[erro] {painel} não encontrado: execute src/calculo.py antes.")
    corpo = MODELO.read_text(encoding="utf-8")
    # <title>, <meta>, <link> e <style> vão para o <head>; o restante para o <body>
    i = corpo.index("</style>") + len("</style>")
    html = CABECALHO + corpo[:i] + "\n</head>\n<body>\n" + corpo[i:] + "\n</body>\n</html>\n"
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text(html, encoding="utf-8")
    shutil.copyfile(painel, site / "painel.json")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copyfile(RAIZ / "web" / "manual.html", site / "manual.html")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import documentos
    except ImportError as e:
        print(f"[aviso] planilhas não geradas ({e}); instale openpyxl (requirements.txt)")
    else:
        sys.argv = [sys.argv[0], str(painel), str(site)]
        documentos.main()
    print(f"[ok] site em {site} ({(site / 'painel.json').stat().st_size / 1e6:.1f} MB de dados)")


if __name__ == "__main__":
    main()
