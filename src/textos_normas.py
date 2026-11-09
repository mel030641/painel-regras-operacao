"""Trechos literais das resoluções citados em cada regra (dica da coluna Norma no painel).

Lê os textos em normas/textos/r<número>_<ano>.txt, separa artigos, parágrafos, incisos,
alíneas e itens de anexo, e devolve, para a citação da regra (campo "artigos"), a lista
[{"ref": "Art. 1º, I, a", "texto": "..."}] com o texto como está no arquivo.

Uso: python src/textos_normas.py   (lista os trechos de todas as regras, para conferência)
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "normas" / "textos"

ROM = {"I": 1, "V": 5, "X": 10, "L": 50}


def rom2int(s: str) -> int:
    s = s.upper()
    t = 0
    for i, c in enumerate(s):
        v = ROM[c]
        t += -v if i + 1 < len(s) and ROM[s[i + 1]] > v else v
    return t


def int2rom(n: int) -> str:
    out = ""
    for v, r in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out += r
            n -= v
    return out


RE_ART = re.compile(r"^Art\.?\s*(\d+)\s*(?:º|°|o\b|\.)?")
RE_PAR = re.compile(r"^§\s*(\d+)")
RE_PU = re.compile(r"^Par[áa]grafo\s+[úu]nico", re.I)
RE_INC = re.compile(r"^([IVXl]{1,6})\s*(?:[-–—.)]|\s-)\s*")
RE_ALI = re.compile(r"^([a-z])\)\s")
RE_ANEXO = re.compile(r"^ANEXO\s*(\d+|[IVX]+)?\b", re.I)
RE_ITEM = re.compile(r"^(\d+)\)\s")
RE_FIM = re.compile(r"^(Este texto não substitui|[A-ZÀ-Ú][A-ZÀ-Ú .]{6,}$)")


def _norm(s: str) -> str:
    return s.replace(" ", " ").replace("​", "").rstrip()


def _tabela_estagios(linhas: list[str]) -> list[str]:
    """Res. 1.382/2015, art. 1º, V: a tabela vem uma célula por linha; remonta uma linha por reservatório."""
    try:
        i = linhas.index("Ordem de deplecionamento")
    except ValueError:
        return linhas
    j = i + 1
    while j < len(linhas) and not re.match(r"^\d+ª$", linhas[j].strip()):
        j += 1
    out = linhas[:i] + ["Ordem de deplecionamento | reservatório | estágios 1º, 2º e 3º (% do volume útil):"]
    while j + 4 < len(linhas) and re.match(r"^\d+ª$", linhas[j].strip()):
        o, nome, a, b, c = (x.strip() for x in linhas[j:j + 5])
        out.append(f"{o} {nome}: {a} / {b} / {c}")
        j += 5
    return out + linhas[j:]


def _romano_l(s: str) -> str:
    """Alguns textos usam a letra l no lugar do I romano (lll. → III.)."""
    return re.sub(r"^(l{1,3})(?=[.\s\u00a0])", lambda m: "I" * len(m.group(1)), s)


@lru_cache(maxsize=None)
def estrutura(arquivo: str) -> dict:
    """{'arts': {n: {'linhas': [...], 'par': {k: [...]}, 'inc': {(par, i): [...]}, 'ali': {(par, i, a): [...]}}},
    'anexos': {n: {'linhas': [...], 'itens': {k: [...]}}}}"""
    linhas = [_romano_l(_norm(l).strip()) for l in (DIR / arquivo).read_text(encoding="utf-8").splitlines()]
    linhas = _tabela_estagios(linhas)
    arts: dict = {}
    anexos: dict = {}
    art = par = inc = None
    anexo = item = None
    for ln in linhas:
        s = ln.strip()
        if not s:
            continue
        m = RE_ANEXO.match(s)
        if m and (s.upper() == s or len(s) < 20):
            n = m.group(1) or "1"
            anexo = rom2int(n) if not n.isdigit() else int(n)
            anexos[anexo] = {"linhas": [], "itens": {}}
            art = item = None
            continue
        if anexo is not None:
            if RE_FIM.match(s) and not RE_ITEM.match(s):
                continue
            mi = RE_ITEM.match(s)
            if mi:
                item = int(mi.group(1))
                anexos[anexo]["itens"][item] = []
            anexos[anexo]["linhas"].append(s)
            if item is not None:
                anexos[anexo]["itens"][item].append(s)
            continue
        m = RE_ART.match(s)
        if m:
            art = int(m.group(1))
            arts[art] = {"linhas": [s], "par": {}, "inc": {}, "ali": {}}
            par = inc = None
            continue
        if art is None:
            continue
        if RE_FIM.match(s):
            art = None
            continue
        a = arts[art]
        a["linhas"].append(s)
        if RE_PU.match(s):
            par, inc = 1, None
            a["par"][1] = [s]
            continue
        mp = RE_PAR.match(s)
        if mp:
            par, inc = int(mp.group(1)), None
            a["par"][par] = [s]
            continue
        mi = RE_INC.match(s)
        if mi and not RE_ALI.match(s):
            inc = rom2int(mi.group(1).replace("l", "I"))
            a["inc"][(par, inc)] = [s]
            if par is not None:
                a["par"][par].append(s)
            continue
        ma = RE_ALI.match(s)
        if ma and inc is not None:
            a["ali"][(par, inc, ma.group(1))] = [s]
            a["inc"][(par, inc)].append(s)
            if par is not None:
                a["par"][par].append(s)
            continue
        # continuação (tabela ou linha quebrada)
        if inc is not None:
            a["inc"][(par, inc)].append(s)
        if par is not None:
            a["par"][par].append(s)
    return {"arts": arts, "anexos": anexos}


def caput(a: dict) -> str:
    return a["linhas"][0]


def arquivo_da(resolucao: str) -> str | None:
    m = re.search(r"([\d.]+)/(\d{4})", resolucao)
    if not m:
        return None
    nome = f"r{m.group(1).replace('.', '')}_{m.group(2)}.txt"
    return nome if (DIR / nome).exists() else None


ORD = lambda n: f"{n}º" if n < 10 else str(n)


def _num(tok: str) -> int | None:
    m = re.fullmatch(r"(\d+)\s*º?", tok.strip())
    return int(m.group(1)) if m else None


def parse_citacao(cit: str) -> list[tuple]:
    """Converte 'arts. 3º, I e § 1º; 5º; 12' em refs: ('art', n), ('inc', n, par, i), ('ali', n, par, i, a),
    ('par', n, k), ('anexo', n), ('item', anexo, k)."""
    cit = re.sub(r"\(.*?\)", "", cit)
    cit = re.sub(r"\barts?\.\s*", "", cit)
    refs: list[tuple] = []
    for grupo in [g.strip() for g in cit.split(";") if g.strip()]:
        ma = re.match(r"Anexo\s*(\d+)(?:,\s*item\s*(\d+))?", grupo, re.I)
        if ma:
            ax = int(ma.group(1))
            refs.append(("item", ax, int(ma.group(2))) if ma.group(2) else ("anexo", ax))
            continue
        toks = [t for t in re.split(r",\s*|\s+e\s+", grupo) if t.strip()]
        art = par = inc = None
        modo_par = False
        for t in toks:
            t = t.strip()
            if t == "caput":
                refs.append(("caput", art))
                continue
            if t.startswith("§§"):
                modo_par = True
                t = t[2:].strip()
            elif t.startswith("§"):
                modo_par = False
                k = _num(t[1:])
                refs.append(("par", art, k))
                par = k
                continue
            rng = re.fullmatch(r"(\d+)\s*º?\s+a\s+(\d+)\s*º?", t)
            if rng:
                a0, a1 = int(rng.group(1)), int(rng.group(2))
                if modo_par:
                    refs += [("par", art, k) for k in range(a0, a1 + 1)]
                else:
                    refs += [("art", k) for k in range(a0, a1 + 1)]
                    art = a1
                continue
            n = _num(t)
            if n is not None:
                if modo_par:
                    refs.append(("par", art, n))
                else:
                    art, par, inc = n, None, None
                    refs.append(("art", n))
                continue
            if re.fullmatch(r"[IVX]+", t):
                inc = rom2int(t)
                refs.append(("inc", art, par, inc))
                continue
            if re.fullmatch(r"[a-z]", t):
                refs.append(("ali", art, par, inc, t))
                continue
    # um artigo citado junto com seus dispositivos vale só pelos dispositivos
    especificos = {r[1] for r in refs if r[0] in ("inc", "ali", "par", "caput")}
    refs = [r for r in refs if not (r[0] == "art" and r[1] in especificos)]
    # inciso seguido de suas alíneas: fica só com as alíneas
    alis = {(r[1], r[2], r[3]) for r in refs if r[0] == "ali"}
    refs = [r for r in refs if not (r[0] == "inc" and (r[1], r[2], r[3]) in alis)]
    return refs


# citações cuja leitura automática seria ambígua
CORRECOES = {
    ("Res. Conj. ANA/DAEE 614/2010", "arts. 1º, §§ 2º e 3º, 3º e 4º; Anexo 1, item 3"):
        [("par", 1, 2), ("par", 1, 3), ("art", 3), ("art", 4), ("item", 1, 3)],
}


def trechos(resolucao: str, artigos: str) -> list[dict]:
    arq = arquivo_da(resolucao)
    if not arq or not artigos:
        return []
    e = estrutura(arq)
    refs = CORRECOES.get((resolucao, artigos)) or parse_citacao(artigos)
    out = []
    for r in refs:
        tipo = r[0]
        if tipo in ("anexo", "item"):
            ax = e["anexos"].get(r[1])
            if not ax:
                continue
            if tipo == "anexo":
                out.append({"ref": f"Anexo {r[1]}", "texto": "\n".join(ax["linhas"])})
            elif r[2] in ax["itens"]:
                out.append({"ref": f"Anexo {r[1]}, item {r[2]}", "texto": "\n".join(ax["itens"][r[2]])})
            continue
        a = e["arts"].get(r[1])
        if not a:
            continue
        rot = f"Art. {ORD(r[1])}"
        if tipo == "art":
            out.append({"ref": rot, "texto": "\n".join(a["linhas"])})
        elif tipo == "caput":
            out.append({"ref": rot + ", caput", "texto": caput(a)})
        elif tipo == "par":
            p = a["par"].get(r[2])
            if p:
                rp = "parágrafo único" if r[2] == 1 and RE_PU.match(p[0]) else f"§ {ORD(r[2])}"
                out.append({"ref": f"{rot}, {rp}", "texto": "\n".join(p)})
        elif tipo == "inc":
            i = a["inc"].get((r[2], r[3]))
            if i:
                pre = caput(a) if r[2] is None else a["par"][r[2]][0]
                rp = "" if r[2] is None else f", § {ORD(r[2])}"
                out.append({"ref": f"{rot}{rp}, {int2rom(r[3])}", "texto": pre + "\n" + "\n".join(i)})
        elif tipo == "ali":
            al = a["ali"].get((r[2], r[3], r[4]))
            if al:
                pre = [caput(a), a["inc"][(r[2], r[3])][0]]
                out.append({"ref": f"{rot}, {int2rom(r[3])}, {r[4]}", "texto": "\n".join(pre + al)})
    # junta trechos repetidos (ex.: mesmo caput em incisos seguidos)
    vistos, unicos = set(), []
    for t in out:
        if t["ref"] not in vistos:
            vistos.add(t["ref"])
            unicos.append(t)
    return unicos


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import regras
    vistos = set()
    for rg in regras.REGRAS:
        k = (rg["resolucao"], rg.get("artigos", ""))
        if k in vistos:
            continue
        vistos.add(k)
        ts = trechos(*k)
        print(f"\n##### {k[0]} | {k[1]}  -> {[t['ref'] for t in ts]}")
        for t in ts:
            print(f"  [{t['ref']}] {t['texto'][:160]!r}{' …' if len(t['texto']) > 160 else ''} ({len(t['texto'])} c)")
