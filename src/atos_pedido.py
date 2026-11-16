"""atos_pedido.py - Aplica ao cadastro (config/atos_complementares.json) o registro de um pedido de cadastro de ato.

O formulário do painel (web/cadastro.html) abre um pedido (issue) no GitHub com o registro do ato num bloco ```json.
A automação (.github/workflows/atos_cadastro.yml) chama este script com o texto do pedido, que:
  1. extrai o primeiro bloco ```json do texto;
  2. confere o registro com as mesmas regras de src/atos.py e confere se as regras citadas existem em src/regras.py;
  3. inclui o registro no cadastro (ou substitui o de mesmo id) e grava o arquivo.

Uso: python src/atos_pedido.py ARQUIVO_COM_O_TEXTO_DO_PEDIDO [--saida-github ARQUIVO]
Sai com código 1 e mensagem legível se o pedido for inválido; nada é gravado nesse caso.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atos  # noqa: E402

BLOCO = re.compile(r"```json\s*\n(.*?)\n```", re.S)
CAMPOS = ("id", "tipo", "documento", "data", "sei", "regras", "vigencia", "citacao", "efeito") + atos.RASTREIO
ID = re.compile(r"^[A-Za-z0-9_.-]{3,80}$")


class PedidoInvalido(ValueError):
    pass


def extrair(texto: str) -> dict:
    m = BLOCO.search(texto.replace("\r\n", "\n"))
    if not m:
        raise PedidoInvalido("o pedido não tem um bloco ```json com o registro do ato")
    try:
        reg = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise PedidoInvalido(f"o bloco json não é válido: {e}") from e
    if not isinstance(reg, dict):
        raise PedidoInvalido("o registro deve ser um objeto json")
    extras = set(reg) - set(CAMPOS)
    if extras:
        raise PedidoInvalido(f"campos não reconhecidos: {sorted(extras)}")
    if not ID.match(str(reg.get("id", ""))):
        raise PedidoInvalido("id ausente ou com caracteres fora de A-Z, a-z, 0-9, _ . -")
    return {k: reg[k] for k in CAMPOS if k in reg}


def aplicar(reg: dict, caminho: Path = atos.ARQUIVO) -> tuple[str, list[dict]]:
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    lista = dados.get("atos", [])
    acao = "inclui"
    for i, a in enumerate(lista):
        if a.get("id") == reg["id"]:
            lista[i], acao = reg, "substitui"
            break
    else:
        lista.append(reg)
    try:
        atos._conferir(lista)
    except atos.CadastroInvalido as e:
        raise PedidoInvalido(str(e).split(":\n", 1)[-1].strip()) from e
    import regras  # noqa: E402  (só aqui: importa o cadastro atual)
    ids = {r["id"] for r in regras.REGRAS}
    falta = [r for r in reg.get("regras", []) if r not in ids]
    if falta:
        raise PedidoInvalido(f"regras citadas que não existem em src/regras.py: {falta}")
    dados["atos"] = lista
    return acao, dados


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pedido", help="arquivo com o texto do pedido (corpo da issue)")
    ap.add_argument("--saida-github", help="arquivo GITHUB_OUTPUT para id, acao e documento")
    ap.add_argument("--cadastro", default=str(atos.ARQUIVO))
    a = ap.parse_args()
    caminho = Path(a.cadastro)
    try:
        reg = extrair(Path(a.pedido).read_text(encoding="utf-8"))
        acao, dados = aplicar(reg, caminho)
    except PedidoInvalido as e:
        print(f"[erro] pedido inválido: {e}")
        sys.exit(1)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[ok] {acao} o ato {reg['id']} ({reg.get('documento')})")
    if a.saida_github:
        with open(a.saida_github, "a", encoding="utf-8") as f:
            doc = str(reg.get("documento", "")).replace("\n", " ")[:120]
            f.write(f"id={reg['id']}\nacao={acao}\ndocumento={doc}\n")


if __name__ == "__main__":
    main()
