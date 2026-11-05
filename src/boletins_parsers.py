"""Leitores das faixas de operação publicadas nos boletins diários da ANA (cópia de historico_faixas/scripts/extrair_faixas.py,
usada por boletins_ana.py na coleta diária). Textos gerados pelo pdftotext (-layout e -bbox).
Uso: python extrair_faixas.py <pasta_textos> <inventario_gov.csv> <saida_diario.csv>
Saída: uma linha por boletim × reservatório (evidência). Sem dependências externas."""
import csv, html, os, re, sys, datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # regras.py ao lado (painel-normas/src)
try:
    from regras import FAIXAS as _F
    FAIXAS_NOMES = {k: v["nomes"] for k, v in _F.items()}
except Exception:  # sem o painel ao lado: nomes padrão
    FAIXAS_NOMES = {}

MESES = {m: i + 1 for i, m in enumerate("janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro".split())}
NOMES_CANT = {1: "Faixa 1 - Normal", 2: "Faixa 2 - Atenção", 3: "Faixa 3 - Alerta", 4: "Faixa 4 - Restrição", 5: "Faixa 5 - Especial"}
NOMES_SF = {"NORMAL": ("Normal", 0), "ATENÇÃO": ("Atenção", 1), "ATENCAO": ("Atenção", 1), "RESTRIÇÃO": ("Restrição", 2), "RESTRICAO": ("Restrição", 2)}
CAMPOS = ["rid", "reservatorio", "sistema_boletim", "data_boletim", "faixa_publicada", "idx", "texto_original",
          "data_referencia", "valor_referencia", "unidade", "periodo_aplicacao", "condicao_limites", "url_oficial",
          "arquivo_texto", "versao_parser", "status_extracao"]


def palavras(p1):
    s = open(p1, encoding="utf-8", errors="ignore").read()
    return [(float(x), float(y), html.unescape(w)) for x, y, w in
            re.findall(r'xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]*)<', s)]


def cantareira(base, meta):
    W = palavras(base + ".p1.html")
    ancora = [w for w in W if w[2] == "Em" and any(v[2] == "cumprimento" and abs(v[1] - w[1]) < 3 for v in W)]
    linha = dict(rid="CANTAREIRA", reservatorio="Sistema Equivalente Cantareira", versao_parser="cant_v1")
    if not ancora:
        return [dict(linha, status_extracao="texto do Art. 6º não encontrado")]
    a = ancora[0]
    col = sorted((w for w in W if w[0] >= a[0] * 0.975 and  # margem proporcional à escala da página
                   a[1] - 2 <= w[1] < a[1] + 500), key=lambda w: (round(w[1] / 4), w[0]))
    txt = " ".join(w[2] for w in col)
    txt_limpo = re.sub(r"\b(Orde|Ordem|m)\b ", "", txt)  # cabeçalho da tabela vizinha
    txt_limpo = re.sub(r"(?<=[,a-zà-ú]) \d (?=[a-zà-ú\d])", " ", txt_limpo)  # dígitos soltos da coluna "Ordem"
    m = re.search(r"em (\d{1,2}) de (\w+) de (\d{4}),\s*(?:\d\s+)?(\d{1,3},\d{1,2})% de seu volume.*?no mês de (\w+) de (\d{4}),? será a\s+Faixa (\d)", txt_limpo)
    if not m:
        return [dict(linha, texto_original=txt[:400], status_extracao="padrão não reconhecido")]
    d, mes, a_, vu, mes_ap, a_ap, fx = m.groups()
    fx = int(fx)
    return [dict(linha, faixa_publicada=NOMES_CANT[fx], idx=fx - 1, texto_original=txt[txt.find("Em cumprimento"):][:420],
                 data_referencia=dt.date(int(a_), MESES[mes.lower()], int(d)).isoformat(), valor_referencia=vu.replace(",", "."),
                 unidade="% VU", periodo_aplicacao=f"{a_ap}-{MESES[mes_ap.lower()]:02d}", status_extracao="ok")]


def per_sf(e):
    """Mês de aplicação: faixa estabelecida no fim do mês (dia ≥ 25) vale para o mês seguinte."""
    d = dt.date(int(e[2]), int(e[1]), int(e[0]))
    if d.day >= 25:
        d = (d.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    return d.strftime("%Y-%m")


def sao_francisco(base, meta):
    L = open(base + ".txt", encoding="utf-8", errors="ignore").read().splitlines()
    out = []
    i = next((k for k, l in enumerate(L) if "Faixa de Operação de Três Marias" in l), None)
    if i is None:
        return [dict(rid=r, reservatorio=n, versao_parser="sf", status_extracao="cartão de faixa não encontrado")
                for r, n in (("SFTMAR", "Três Marias"), ("SFSOBR", "Sobradinho"))]
    corte = L[i].find("Faixa de Operação de Sobradinho")
    bloco = L[i:i + 14]
    est = [re.findall(r"Estabelecida em (\d{2})/(\d{2})/(\d{4}) \( ?V\.U\. = ([\d,]+)%", l) for l in bloco]
    est = next((e for e in est if e), [])
    linha_fx = next((l for l in bloco[1:] if re.search(r"\b(NORMAL|ATENÇÃO|RESTRIÇÃO)\b", l)), "")
    fxs = [(m.start(), m.group(1)) for m in re.finditer(r"\b(NORMAL|ATENÇÃO|RESTRIÇÃO)\b", linha_fx)]
    # limites: primeira linha numérica/LIVRE após o cabeçalho de vazões
    j = next((k for k, l in enumerate(bloco) if "Vazão Máxima Mensal" in l), None)
    lim = ""
    if j is not None:  # corta à direita da 5ª coluna para não pegar o eixo do gráfico vizinho
        cab = bloco[j]; pos = [m.start() for m in re.finditer(r"Vazão M", cab)]
        fim = (pos[4] + 25) if len(pos) >= 5 else len(cab)
        lim = next((l[:fim] for l in bloco[j + 1:] if re.search(r"\b(LIVRE|\d{2,5})\b", l[:fim]) and "m³/s" not in l), "")
    vals = re.findall(r"LIVRE|\d[\d.]*", lim)
    for k, (rid, nome) in enumerate((("SFTMAR", "Três Marias"), ("SFSOBR", "Sobradinho"))):
        cand = [f for pos, f in fxs if (pos < corte) == (k == 0)] if corte > 0 else [f for _, f in fxs][k:k + 1]
        linha = dict(rid=rid, reservatorio=nome, versao_parser="sf_v2" if est else "sf_v1")
        if not cand:
            out.append(dict(linha, status_extracao="faixa não encontrada")); continue
        nome_fx, idx = NOMES_SF[cand[0]]
        e = est[k] if len(est) > k else None
        cond = ""
        if len(vals) >= 5:
            cond = (f"Vazão máx. mensal Três Marias {vals[0]}; mín. diária {vals[1]} m³/s" if k == 0 else
                    f"Vazão máx. mensal Xingó {vals[2]}; mín. diária Sobradinho {vals[3]} m³/s; mín. diária Xingó {vals[4]} m³/s")
        out.append(dict(linha, faixa_publicada=nome_fx, idx=idx, texto_original=re.sub(r"\s+", " ", " ".join(bloco[:4]))[:300],
                        data_referencia=(dt.date(int(e[2]), int(e[1]), int(e[0])).isoformat() if e else ""),
                        valor_referencia=(e[3].replace(",", ".") if e else ""), unidade="% VU" if e else "",
                        # vigência = mês em que a faixa foi estabelecida (boletins do início do mês ainda podem trazer a anterior)
                        periodo_aplicacao=(per_sf(e) if e else meta["data_publicacao"][:7]), condicao_limites=cond, status_extracao="ok"))
    return out


# ---------------------------------------------------------------- boletins com layout gráfico (coordenadas)
NOMES_FX = {"normal": "Normal", "atenção": "Atenção", "alerta": "Alerta", "restrição": "Restrição"}


def paginas(arq):
    """Lista de páginas; cada página é uma lista de linhas (y, [(x, palavra), ...]) ordenadas."""
    s = open(arq, encoding="utf-8", errors="ignore").read()
    out = []
    for pg in s.split("<page ")[1:]:
        W = [(float(x), float(y), html.unescape(w)) for x, y, w in re.findall(r'xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]*)<', pg)]
        L = {}
        for x, y, w in sorted(W, key=lambda t: (round(t[1] / 6), t[0])):
            L.setdefault(round(y / 6) * 6, []).append((x, w))
        out.append(sorted(L.items()))
    return out


def achar(linhas, seq, xmin=-1, xmax=1e9):
    """Ocorrências (índice_linha, x) da sequência de palavras `seq` numa linha, com x inicial no intervalo."""
    r = []
    for i, (y, ws) in enumerate(linhas):
        pal = [w for _, w in ws]
        for k in range(len(pal) - len(seq) + 1):
            if pal[k:k + len(seq)] == seq and xmin <= ws[k][0] <= xmax:
                r.append((i, ws[k][0], k))
    return r


def texto_regiao(linhas, i0, x0, x1, dy=250):
    """Textos das linhas abaixo de i0 (até dy pontos) restritos à faixa horizontal [x0, x1]."""
    y0, out = linhas[i0][0], []
    for y, ws in linhas[i0 + 1:]:
        if y - y0 > dy:
            break
        t = " ".join(w for x, w in ws if x0 <= x <= x1)
        if t:
            out.append(t)
    return out


def nome_faixa(t):
    m = re.search(r"(normal|atenção|alerta|restrição)", t.lower())
    return NOMES_FX[m.group(1)] if m else None


def paranapanema(base, meta):
    UHES = {"Jurumirim": "PPJURU", "Chavantes": "PPCHAV", "Capivara": "PPCAPI"}
    out = []
    for pg in paginas(base + ".bbox.html"):
        for i, x, k in achar(pg, ["Faixa", "de", "Operação"]):
            pal = [w for _, w in pg[i][1]]
            uhe = next((pal[j + 1] for j in range(len(pal) - 1) if pal[j] == "UHE" and pal[j + 1] in UHES), None)
            if not uhe:  # layout de 2024: título da UHE em outra linha da mesma página
                todos = [w for _, ws in pg for _, w in ws]
                uhe = next((todos[j + 1] for j in range(len(todos) - 1) if todos[j] == "UHE" and todos[j + 1] in UHES), None)
            if not uhe:
                continue
            esc = x / 1463  # escala da página (boletins de 2024 têm coordenadas ~metade)
            reg = texto_regiao(pg, i, x - 250 * esc, x + 450 * esc, 220 * esc)
            fx = next((nome_faixa(t) for t in reg if nome_faixa(t)), None)
            j = next((n for n, t in enumerate(reg) if "Vazão defluente" in t), None)
            lim = f"{reg[j]}: {reg[j + 1]}" if j is not None and j + 1 < len(reg) else ""
            rid = UHES[uhe]; nomes = FAIXAS_NOMES.get(rid)
            out.append(dict(rid=rid, reservatorio=uhe, faixa_publicada=fx or "", idx=(nomes.index(fx) if fx in (nomes or []) else ""),
                            texto_original=" / ".join(reg)[:300], periodo_aplicacao=meta["periodo"], condicao_limites=lim,
                            versao_parser="pp_v1", status_extracao="ok" if fx else "faixa não encontrada"))
    return out or [dict(rid="PP", versao_parser="pp_v1", status_extracao="bloco de faixa não encontrado")]


def tocantins(base, meta):
    for pg in paginas(base + ".bbox.html")[:2]:
        for i, x, k in achar(pg, ["Faixa", "de", "Operação"]):
            reg = texto_regiao(pg, i, x - 80, x + 350, 80)
            if not any(nome_faixa(t) for t in reg):
                continue
            mes = next((t for t in reg if re.match(r"\w+/\d{4}", t)), "")
            fx = next((nome_faixa(t) for t in reg if nome_faixa(t)), None)
            lim = ""
            for i2, x2, _ in achar(pg, ["Defluência", "Média"]):
                r2 = texto_regiao(pg, i2, x2 - 80, x2 + 350, 90)
                if any("Máxima" in t for t in r2):
                    lim = "Defluência média máxima mensal: " + next((t for t in r2 if not re.search(r"Máxima|Mensal|\w+/\d{4}", t)), ""); break
            mm = re.match(r"(\w+)/(\d{4})", mes)
            per = f"{mm.group(2)}-{MESES[mm.group(1).lower()]:02d}" if mm and mm.group(1).lower() in MESES else meta["periodo"]
            return [dict(rid="TOSMES", reservatorio="Serra da Mesa", faixa_publicada=fx or "",
                         idx=(FAIXAS_NOMES["TOSMES"].index(fx) if fx in FAIXAS_NOMES["TOSMES"] else ""),
                         texto_original=" / ".join(reg)[:300], periodo_aplicacao=per, condicao_limites=lim,
                         versao_parser="to_v1", status_extracao="ok" if fx else "faixa não encontrada")]
    return [dict(rid="TOSMES", reservatorio="Serra da Mesa", versao_parser="to_v1", status_extracao="bloco de faixa não encontrado")]


def grande_paranaiba(base, meta):
    UHES = [("Furnas", "GRFURN", "Furnas"), ("Peixoto", "GRMMOR", "Mascarenhas de Moraes (Peixoto)"),
            ("Mascarenhas", "GRMMOR", "Mascarenhas de Moraes (Peixoto)"),
            ("Emborcação", "PREMBO", "Emborcação"), ("Itumbiara", "PRITUM", "Itumbiara")]
    achados = {}
    for pg in paginas(base + ".bbox.html"):
        rotulos = []  # (índice da linha, rid, nome) para nomes de UHE na margem esquerda (páginas de detalhe)
        for i, (y, ws) in enumerate(pg):
            if ws and ws[0][0] < 120:
                t = " ".join(w for _, w in ws if _ < 900)
                for chave, rid, nome in UHES:
                    if chave in t:
                        rotulos.append((i, rid, nome)); break
        for i, x, k in achar(pg, ["Faixa", "de", "Operação"], xmax=400):
            ant = [r for r in rotulos if r[0] <= i]
            if not ant:
                continue
            _, rid, nome = ant[-1]
            ws = pg[i][1]
            resto = " ".join(w for xx, w in ws[k + 3:] if xx < 1900)
            if i + 1 < len(pg) and pg[i + 1][0] - pg[i][0] <= 12:  # condição impressa meia linha abaixo
                resto += " " + " ".join(w for xx, w in pg[i + 1][1] if 800 <= xx < 1900)
            fx = nome_faixa(resto.split(" Vazão")[0].split(" Sem")[0]) or nome_faixa(resto)
            cond = re.sub(r"^(de )?(Normal|Atenção|Alerta|Restrição)\s*", "", resto).strip()
            if rid not in achados:
                achados[rid] = dict(rid=rid, reservatorio=nome, faixa_publicada=fx or "",
                                    idx=(FAIXAS_NOMES[rid].index(fx) if fx in FAIXAS_NOMES[rid] else ""),
                                    texto_original=("Faixa de Operação " + resto)[:300], periodo_aplicacao=meta["periodo"],
                                    condicao_limites=cond, versao_parser="gp_v1",
                                    status_extracao="ok" if fx else "faixa não encontrada")
    return list(achados.values()) or [dict(rid="GP", versao_parser="gp_v1", status_extracao="bloco de faixa não encontrado")]


PARSERS = {"02": ("Cantareira (02)", cantareira), "12": ("São Francisco (12)", sao_francisco),
           "08": ("Paranapanema (08)", paranapanema), "13": ("Tocantins (13)", tocantins), "14": ("Grande e Paranaíba (14)", grande_paranaiba)}


def main():
    pasta, inv, saida = sys.argv[1:4]
    linhas = []
    for r in csv.DictReader(open(inv, encoding="utf-8")):
        if r["codigo"] not in PARSERS:
            continue
        base = os.path.join(pasta, f"{r['codigo']}_{r['data_publicacao']}_{os.path.basename(r['url'])[:-4]}")
        sist, fn = PARSERS[r["codigo"]]
        comum = dict(sistema_boletim=sist, data_boletim=r["data_publicacao"], url_oficial=r["url"], arquivo_texto=os.path.basename(base))
        if not os.path.exists(base + ".txt"):
            continue  # boletim não baixado (só o do meio de cada vigência é coletado)
        d = dt.date.fromisoformat(r["data_publicacao"])
        r["periodo"] = ((d - dt.timedelta(days=(d.weekday() - 5) % 7)).isoformat() if r["codigo"] == "08" else d.strftime("%Y-%m"))
        try:
            for l in fn(base, r):
                linhas.append({**comum, **l})
        except Exception as e:  # registra e segue
            linhas.append(dict(comum, status_extracao=f"erro: {e}"))
    with open(saida, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS, delimiter=";"); w.writeheader(); w.writerows(linhas)
    ok = sum(l.get("status_extracao") == "ok" for l in linhas)
    print(f"{ok}/{len(linhas)} linhas ok")


if __name__ == "__main__":
    main()
