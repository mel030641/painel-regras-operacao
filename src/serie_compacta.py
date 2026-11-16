"""serie_compacta.py - Formato compacto da série diária de cada regra no painel.json.

No formato expandido (usado no cálculo, nas planilhas e na tela), a série é uma lista com um objeto por dia:
    [{"d": "2023-01-01", "v": 149.0, "st": "Atende", "obs": "..."}, ...]
No arquivo, cada regra guarda a série em colunas, com os dias consecutivos a partir de d0:
    {"d0": "2023-01-01", "n": 1400, "v": [149.0, ...], "st": [3, ...], "obs": [null, 7, ...]}
Os campos de texto viram números que apontam para a lista "textos" do painel.json, montada a cada execução do
cálculo. null = campo ausente naquele dia. A conversão é exata nos dois sentidos (expandir(compactar(x)) == x).

Uso: python src/serie_compacta.py [painel.json]   (confere a ida e volta e mostra o ganho de tamanho)
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

# campos com texto que se repete: guardados por referência à lista "textos"
TEXTO = ("st", "faixa", "orig", "obs", "parc", "ag", "lado")


def _dias(d0: str, n: int) -> list[str]:
    t = dt.date.fromisoformat(d0)
    return [(t + dt.timedelta(days=i)).isoformat() for i in range(n)]


def compactar(painel: dict) -> dict:
    """Devolve uma cópia do painel com as séries em colunas e a lista "textos". Não altera o original."""
    textos: list[str] = []
    pos: dict[str, int] = {}

    def ref(s):
        if s not in pos:
            pos[s] = len(textos)
            textos.append(s)
        return pos[s]

    regras = []
    for r in painel["regras"]:
        serie = r.get("serie")
        if not isinstance(serie, list) or not serie:
            regras.append(r)
            continue
        dias = [p["d"] for p in serie]
        if dias != _dias(dias[0], len(dias)):
            raise ValueError(f"{r['id']}: série com dias fora de sequência; formato compacto exige dias consecutivos")
        campos = []
        for p in serie:
            for k in p:
                if k != "d" and k not in campos:
                    campos.append(k)
        col: dict = {"d0": dias[0], "n": len(dias)}
        for k in campos:
            if k in TEXTO:
                col[k] = [ref(p[k]) if k in p else None for p in serie]
            else:
                col[k] = [p.get(k) for p in serie]
            if not any(k in p and p[k] is None for p in serie):
                continue
            raise ValueError(f"{r['id']}: campo '{k}' com valor nulo explícito; o formato compacto usa null para ausente")
        regras.append({**r, "serie": col})
    return {**painel, "formato_serie": "colunas", "textos": textos, "regras": regras}


def expandir(painel: dict) -> dict:
    """Converte, no próprio dicionário, as séries em colunas de volta para a lista de objetos por dia."""
    if painel.get("formato_serie") != "colunas":
        return painel
    textos = painel["textos"]
    for r in painel["regras"]:
        col = r.get("serie")
        if not isinstance(col, dict):
            continue
        campos = [k for k in col if k not in ("d0", "n")]
        serie = []
        for i, d in enumerate(_dias(col["d0"], col["n"])):
            p = {"d": d}
            for k in campos:
                v = col[k][i]
                if v is not None:
                    p[k] = textos[v] if k in TEXTO else v
            serie.append(p)
        r["serie"] = serie
    del painel["formato_serie"], painel["textos"]
    return painel


def ler(caminho: Path) -> dict:
    """Lê o painel.json (compacto ou não) e devolve as séries no formato expandido."""
    return expandir(json.loads(Path(caminho).read_text(encoding="utf-8")))


if __name__ == "__main__":
    import copy
    import sys
    p = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "painel.json")
    orig = ler(p)
    comp = compactar(orig)
    volta = expandir(copy.deepcopy(comp))
    a = json.dumps(orig, ensure_ascii=False, separators=(",", ":"))
    b = json.dumps(comp, ensure_ascii=False, separators=(",", ":"))
    print(f"ida e volta exata: {volta == orig}")
    print(f"tamanho: {len(a.encode()) / 1e6:.1f} MB -> {len(b.encode()) / 1e6:.1f} MB; textos distintos: {len(comp['textos'])}")
