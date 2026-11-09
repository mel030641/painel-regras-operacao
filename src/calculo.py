"""
calculo.py - Calcula status de cumprimento e folga de cada regra (src/regras.py) e gera data/painel.json.

Uso:
    python src/calculo.py                       # avalia de 01/01/2024 até hoje
    python src/calculo.py --inicio 2026-10-01 --fim 2026-10-31
    python src/calculo.py --meses-horarios 36   # quantos meses de dado horário do ONS baixar (padrão 36)

Status: Atende | Tolerância | Não atende | Em curso | Não se aplica | Sem dado | Parâmetro pendente.
Folga: margem até o limite, na unidade da regra (positiva = dentro do limite) e em % do limite.
Dia mais recente do ONS/SAR (D-1) costuma vir incompleto: é marcado como provisório.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coleta  # noqa: E402
from regras import FAIXAS, PARAMETROS, REGRAS, VIGENCIA, VIGENCIA_A_CONFERIR  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "config"
SAIDA = RAIZ / "data" / "painel.json"
DIAS_SERIE = 4000  # dias de série enviados ao painel (cobre desde out/2017)
VU_CANTAREIRA_HM3 = 981.56  # 925/2017, art. 1º, § 1º
CANTAREIRA_SAR = ["29001", "29002", "29003", "29004"]

OK, TOL, NAO, NA, SD, PP = "Atende", "Tolerância", "Não atende", "Não se aplica", "Sem dado", "Parâmetro pendente"
EC = "Em curso"  # janela (semana operativa ou mês) ainda aberta: o status definitivo só sai no fechamento


def em_curso(out: dict, d: dt.date, janela: str) -> dict:
    """Janela aberta: guarda o status parcial em 'parc', marca 'exc' se a média parcial excede o limite e devolve 'Em curso'."""
    if janela != "dia" and fim_janela(d, janela) > d and out["st"] in (OK, TOL, NAO):
        out["parc"] = out["st"]
        if out.get("folga") is not None and out["folga"] < 0:
            out["exc"] = True
        out["st"] = EC
    return out


# ============================================================================ carga de dados
class Dados:
    def __init__(self, inicio: dt.date, fim: dt.date, meses_horarios: int):
        self.inicio, self.fim = inicio, fim
        fontes = set()
        for r in REGRAS:
            for k in ("fonte", "condicao_fonte", "soma_transf"):
                if r.get(k):
                    fontes.add(r[k])
            for f in r.get("fontes", []) if isinstance(r.get("fontes"), list) else []:
                fontes.add(f)
            if isinstance(r.get("fontes"), dict):
                fontes.update(f"ONS:{i}:vu" for i in r["fontes"])
        expand = set()
        for f in fontes:
            if f.startswith("SOMA:"):
                expand.update(f[5:].split("+"))
            else:
                expand.add(f)
        ids_d = {f.split(":")[1] for f in expand if f.startswith("ONS:")} | {k for k in FAIXAS if k != "CANTAREIRA"} | {"RIUHFC"}
        ids_h = {f.split(":")[1] for f in expand if f.startswith("ONSH:")}
        self.sar_cod = {f.split(":")[1] for f in expand if f.startswith("SAR:")} | set(CANTAREIRA_SAR)
        self.saisp = {f.split(":")[1] for f in expand if f.startswith("SAISP:")}
        self.sabesp_ch = {f.split(":")[1] for f in expand if f.startswith("SABESP:")}
        self.erros: list[str] = []

        anos = list(range(inicio.year - 1, fim.year + 1))
        print(f"[ONS diário] anos {anos}, {len(ids_d)} reservatórios")
        self.ons = coleta.ons_diario(anos, ids_d)

        meses, d = [], dt.date(fim.year, fim.month, 1)
        if meses_horarios <= 0:  # desde o mês de início
            meses_horarios = (fim.year - inicio.year) * 12 + fim.month - inicio.month + 1
        for _ in range(meses_horarios):
            meses.append((d.year, d.month))
            d = (d - dt.timedelta(days=1)).replace(day=1)
        if (inicio.year, inicio.month) not in meses and (inicio.year, inicio.month) >= meses[-1]:
            meses.append((inicio.year, inicio.month))
        print(f"[ONS horário] meses {sorted(meses)}")
        self.onsh = coleta.ons_horario(sorted(meses), ids_h)

        ini_sar = min(inicio, dt.date(fim.year, 1, 1)) - dt.timedelta(days=40)
        self.sar: dict[str, dict] = {}
        for cod in sorted(self.sar_cod):
            try:
                print(f"[SAR] {cod}")
                self.sar[cod] = coleta.sar_cantareira(int(cod), ini_sar, fim)
            except Exception as e:
                self.erros.append(f"SAR {cod}: {e}")
                self.sar[cod] = {}
            # Reserva: dias sem dado no SAR vêm da API Mananciais da Sabesp (mesma medição; conferido em out/2026)
            if cod in coleta.SABESP_CANTAREIRA:
                faltam = (fim - ini_sar).days + 1 - len(self.sar[cod])
                if faltam > 0:
                    try:
                        for d_, v_ in coleta.sabesp_cantareira(cod, ini_sar, fim).items():
                            self.sar[cod].setdefault(d_, v_)
                    except Exception as e:
                        self.erros.append(f"Sabesp (reserva SAR) {cod}: {e}")
        # API Mananciais da Sabesp (Santa Inês etc.). Dia = D-1 07:00 a D 07:00.
        self.sabesp_d: dict[str, dict] = {}
        for chave in sorted(self.sabesp_ch):
            try:
                print(f"[Sabesp] {chave}")
                self.sabesp_d[chave] = coleta.sabesp(chave, ini_sar, fim)
            except Exception as e:
                self.erros.append(f"Sabesp {chave}: {e}")
                self.sabesp_d[chave] = {}
        self.saisp_d: dict[str, dict] = {}
        ini_pcj = inicio - dt.timedelta(days=20)
        for chave in sorted(self.saisp):
            try:
                print(f"[SAISP] {chave}")
                self.saisp_d[chave] = coleta.saisp_diario(coleta.SAISP_POSTOS[chave], ini_pcj, fim)
            except Exception as e:
                self.erros.append(f"SAISP {chave}: {e}")
                self.saisp_d[chave] = {}

        self.faixas_pbi = _ler_json(CONFIG / "faixas.json", {}).get("faixas", {})
        self.param_mensal = _ler_json(CONFIG / "parametros_mensais.json", {}).get("valores", {})

    # ---- séries diárias {data_iso: valor}
    def diaria(self, fonte: str) -> dict[str, float]:
        if fonte.startswith("SOMA:"):
            partes = [self.diaria(p) for p in fonte[5:].split("+")]
            dias = set.intersection(*(set(p) for p in partes)) if partes else set()
            return {d: sum(p[d] for p in partes) for d in dias}
        tipo, chave, *campo = fonte.split(":")
        if tipo == "ONS":
            return {d: v[campo[0]] for d, v in self.ons.get(chave, {}).items() if v.get(campo[0]) is not None}
        if tipo == "SAR":
            return {d: v[campo[0]] for d, v in self.sar.get(chave, {}).items() if v.get(campo[0]) is not None}
        if tipo == "SAISP":
            return dict(self.saisp_d.get(chave, {}))
        if tipo == "SABESP":
            return {d: v[campo[0]] for d, v in self.sabesp_d.get(chave, {}).items() if v.get(campo[0]) is not None}
        if tipo == "MAN":
            return {d: v[campo[0]] for d, v in coleta.manual(chave).items() if v.get(campo[0]) is not None}
        if tipo == "ONSH":  # média diária das horas
            acc = defaultdict(list)
            for h, v in self.onsh.get(chave, {}).items():
                if v.get(campo[0]) is not None:
                    acc[h[:10]].append(v[campo[0]])
            return {d: sum(x) / len(x) for d, x in acc.items()}
        raise ValueError(fonte)

    # ---- séries horárias agrupadas por dia {data_iso: [valores]}
    def horaria(self, fonte: str) -> dict[str, list[float]]:
        _, chave, campo = fonte.split(":")
        acc = defaultdict(list)
        for h, v in self.onsh.get(chave, {}).items():
            if v.get(campo) is not None:
                acc[h[:10]].append(v[campo])
        return acc

    # ---- VU do Sistema Cantareira (% do volume útil total)
    def vu_cantareira(self) -> dict[str, float]:
        dias = set.intersection(*(set(self.sar.get(c, {})) for c in CANTAREIRA_SAR)) if self.sar else set()
        out = {}
        for d in dias:
            vals = [self.sar[c][d].get("vu_hm3") for c in CANTAREIRA_SAR]
            if all(v is not None for v in vals):
                out[d] = sum(vals) / VU_CANTAREIRA_HM3 * 100
        return out


def _ler_json(p: Path, padrao):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return padrao


# ============================================================================ utilidades de calendário
def umido(d: dt.date, meses) -> bool:
    return d.month in meses


def inicio_semana(d: dt.date) -> dt.date:  # semana operativa sábado a sexta (132/2022, art. 8º)
    return d - dt.timedelta(days=(d.weekday() - 5) % 7)


def inicio_janela(d: dt.date, janela: str) -> dt.date:
    if janela == "semana":
        return inicio_semana(d)
    if janela == "mes":
        return d.replace(day=1)
    return d


def fim_janela(d: dt.date, janela: str) -> dt.date:
    if janela == "semana":
        return inicio_semana(d) + dt.timedelta(days=6)
    if janela == "mes":
        prox = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return prox - dt.timedelta(days=1)
    if janela == "ano":
        return dt.date(d.year, 12, 31)
    return d


def dias(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def media(serie: dict[str, float], a: dt.date, b: dt.date):
    v = [serie[x.isoformat()] for x in dias(a, b) if x.isoformat() in serie]
    return (sum(v) / len(v), len(v)) if v else (None, 0)


# ============================================================================ faixas
class Faixas:
    def __init__(self, dados: Dados):
        self.d = dados
        self.cache = {}
        self.divergencias = {}

    def chave_periodo(self, rid: str, d: dt.date) -> tuple[str, dt.date]:
        cfg = FAIXAS[rid]
        if cfg["definicao"] == "semana_sexta":
            ini = inicio_semana(d)
            return ini.isoformat(), ini - dt.timedelta(days=1)
        primeiro = d.replace(day=1)
        if cfg["definicao"] == "mes_dia_anterior":
            return primeiro.strftime("%Y-%m"), primeiro - dt.timedelta(days=1)
        return primeiro.strftime("%Y-%m"), primeiro

    def de(self, rid: str, d: dt.date):
        """Retorna (nome, índice, origem, valor_referência) ou (None, None, motivo, None)."""
        per, ref = self.chave_periodo(rid, d)
        k = (rid, per)
        if k in self.cache:
            return self.cache[k]
        cfg = FAIXAS[rid]
        nomes, lim = cfg["nomes"], cfg["limiares"]
        if "umido" in cfg and umido(d, cfg["umido"]):
            nomes, lim = cfg["nomes_umido"], cfg["limiares_umido"]
        serie = self.d.vu_cantareira() if rid == "CANTAREIRA" else {x: v[cfg["var"]] for x, v in self.d.ons.get(rid, {}).items() if v.get(cfg["var"]) is not None}
        v = serie.get(ref.isoformat())
        if v is None:  # recua até 5 dias se a data de referência faltar
            for j in range(1, 6):
                v = serie.get((ref - dt.timedelta(days=j)).isoformat())
                if v is not None:
                    break
        calc = None if v is None else (nomes[next((i for i, l in enumerate(lim) if v >= l), len(lim))],
                                       next((i for i, l in enumerate(lim) if v >= l), len(lim)), "calculada", round(v, 2))
        pbi = self.d.faixas_pbi.get(f"{rid}|{per}")
        if pbi:  # faixa publicada (boletim ANA ou Power BI) prevalece; divergência com o cálculo é registrada
            if isinstance(pbi, str):
                pbi = {"faixa": pbi}
            nome = pbi.get("faixa")
            idx = pbi.get("idx")
            if idx is None:
                idx = next((i for i, n in enumerate(nomes) if n.lower() == nome.lower()), None)
            res = (nome, idx, pbi.get("origem", "Power BI"), pbi.get("vu_ref") if pbi.get("vu_ref") is not None else (calc[3] if calc else None))
            if calc and calc[1] != idx:
                self.divergencias[k] = {"publicada": nome, "calculada": calc[0], "vu_calculado": calc[3]}
        else:
            res = calc or (None, None, "sem dado de referência", None)
        self.cache[k] = res
        return res


def _historico_estagios(self, regra):
    """Estágio de deplecionamento dia a dia (1.382/2015, art. 1º, V, § 1º).
    Início: menor estágio compatível com os VUs do primeiro dia disponível. Avança para o estágio seguinte quando
    todos os reservatórios atingem o mínimo do estágio atual (com variação absoluta de 5 p.p.); volta ao anterior quando todos
    superam os mínimos do estágio anterior. Interpretação a validar pela área."""
    if "_est" in self.cache:
        return self.cache["_est"]
    tol, mins = regra["tol_pp"], regra["fontes"]  # variação absoluta, em pontos percentuais de VU
    series = {r: self.d.diaria(f"ONS:{r}:vu") for r in mins}
    dias_c = sorted(set.intersection(*(set(s) for s in series.values())))
    hist, est = {}, None
    for d in dias_c:
        vus = {r: series[r][d] for r in mins}
        if est is None:
            est = next((k for k in range(3) if all(vus[r] >= mins[r][k] - tol for r in mins)), 2)
        while est < 2 and all(vus[r] <= mins[r][est] + tol for r in mins):
            est += 1
        while est > 0 and all(vus[r] >= mins[r][est - 1] for r in mins):
            est -= 1
        hist[d] = (est, vus)
    self.cache["_est"] = hist
    return hist


Faixas.estagios = _historico_estagios


# ============================================================================ avaliação
def classificar(valor, limite, sentido: str, tol):
    """sentido 'max' (valor ≤ limite) ou 'min' (valor ≥ limite). Retorna (status, folga, folga_pct)."""
    if limite is None:  # a norma não fixa limite nesta faixa/período: não há obrigação a apurar (decisão SOE, 09/11/2026)
        return NA, None, None
    if valor is None:
        return SD, None, None
    folga = (limite - valor) if sentido == "max" else (valor - limite)
    pct = folga / limite * 100 if limite else None
    if folga >= -1e-9:
        return OK, folga, pct
    if tol and abs(folga) <= abs(limite) * tol + 1e-9:
        return TOL, folga, pct
    return NAO, folga, pct


def _sel(regra, chave, d, idx=None):
    """Seleciona valor de 'tetos'/'pisos' por período (todos/umido/seco) e faixa."""
    tab = regra[chave]
    if "todos" in tab:
        v = tab["todos"]
    else:
        v = tab["umido"] if umido(d, regra.get("umido", [])) else tab["seco"]
    if isinstance(v, list):
        return v[idx] if idx is not None and idx < len(v) else None
    return v


def avaliar(regra, dados: Dados, faixas: Faixas, d: dt.date) -> dict:
    t = regra["tipo"]
    iso = d.isoformat()
    out = {"d": iso, "v": None, "lim": None, "st": SD, "folga": None, "pct": None, "faixa": None, "orig": None, "obs": None}

    vig = VIGENCIA.get(regra["resolucao"])
    if vig and iso < vig:
        out["st"], out["obs"] = NA, f"antes da entrada em vigor ({vig[8:]}/{vig[5:7]}/{vig[:4]})"
        return out
    vac = VIGENCIA_A_CONFERIR.get(regra["resolucao"])
    if vac and iso < vac:
        out["st"], out["obs"] = NA, f"antes de {vac[8:]}/{vac[5:7]}/{vac[:4]}: data de entrada em vigor (comunicado da ANA) a conferir"
        return out

    def com_faixa(rid):
        nome, idx, orig, ref = faixas.de(rid, d)
        out["faixa"], out["orig"] = nome, orig
        return idx

    if t == "teto":
        idx = com_faixa(regra["faixa"])
        if idx is None:
            out["st"] = SD
            out["obs"] = "faixa indisponível"
            return out
        tet = _sel(regra, "tetos", d, idx)
        janela = regra.get("janela", "dia")
        lim = None
        if isinstance(tet, dict):
            janela = tet.get("janela", janela)
            if "param" in tet:
                lim = PARAMETROS[tet["param"]]["valor"]
                out["obs"] = PARAMETROS[tet["param"]]["fonte"]
            else:
                lim = dados.param_mensal.get(f"{tet['param_mensal']}|{d.strftime('%Y-%m')}")
                if lim is None and "limite" in tet:
                    lim, out["obs"] = tet["limite"], "limite legal; verificar o valor mensal fixado pela ANA (não informado)"
                elif lim is not None and "limite" in tet:
                    lim = min(lim, tet["limite"])
            if lim is None:
                out["st"] = PP
                out["obs"] = out["obs"] or "verificar o teto mensal fixado pela ANA (não informado em config/parametros_mensais.json)"
                out["v"] = media(dados.diaria(regra["fonte"]), inicio_janela(d, janela), d)[0]
                return out
        elif tet is not None:
            lim = float(tet)
        serie = dados.diaria(regra["fonte"])
        if iso not in serie:
            return out
        v, _ = media(serie, inicio_janela(d, janela), d)
        out["v"], out["lim"] = v, lim
        if janela != regra.get("janela", "dia"):
            out["ag"] = AGREG_JANELA.get(janela)  # agregação aplicada neste dia difere da padrão da regra
        out["st"], out["folga"], out["pct"] = classificar(v, lim, "max", regra["tol"] if regra["niveis"] == 3 else None)
        if lim is None:
            out["obs"] = "sem limite na faixa (a norma não restringe)"
            return out
        if fim_janela(d, janela) > d and janela != "dia":
            out["obs"] = ((out["obs"] or "") + "; janela em curso").strip("; ")
        return em_curso(out, d, janela)

    if t == "piso":
        idx = None
        if regra.get("faixa"):
            idx = com_faixa(regra["faixa"])
            if regra.get("sem_faixa5") and idx == 4:
                out["st"] = NA
                return out
        lim = _sel(regra, "pisos", d, idx)
        if isinstance(regra["pisos"].get("todos", regra["pisos"].get("umido")), list) and idx is None:
            out["obs"] = "faixa indisponível"
            return out
        serie = dados.diaria(regra["fonte"])
        v = serie.get(iso)
        out["v"], out["lim"] = v, lim
        out["st"], out["folga"], out["pct"] = classificar(v, lim, "min", regra["tol"] if regra["niveis"] == 3 else None)
        return out

    if t == "teto_diario":
        lim = _sel(regra, "tetos", d)
        v = dados.diaria(regra["fonte"]).get(iso)
        if regra.get("sentido") == "inverso" and v is not None:
            # a Sabesp publica o bombeamento Atibainha -> Jaguari como valor negativo
            if v >= 0:
                out["st"], out["lim"], out["obs"] = NA, lim, "sem bombeamento no sentido inverso"
                return out
            v = -v
        out["v"], out["lim"] = v, lim
        out["st"], out["folga"], out["pct"] = classificar(v, lim, "max", None)
        return out

    if t == "vu_min":
        nome, idx, orig, _ = faixas.de(regra["condicao"]["faixa"], d)
        out["faixa"], out["orig"] = (f"{FAIXAS_NOME.get(regra['condicao']['faixa'], '')}: {nome}" if nome else None), orig
        v = dados.diaria(regra["fonte"]).get(iso)
        out["v"], out["lim"] = v, regra["minimo"]
        if nome is None:
            out["obs"] = "faixa da condição indisponível"
            return out
        if nome not in regra["condicao"]["em"]:
            out["st"] = NA
            return out
        out["st"], out["folga"], out["pct"] = classificar(v, regra["minimo"], "min", None)
        return out

    if t in ("intervalo", "intervalo_se"):
        v = dados.diaria(regra["fonte"]).get(iso)
        if t == "intervalo_se":
            q = dados.diaria(regra["condicao_fonte"]).get(iso)
            if q is not None and q <= 0:
                out["v"], out["st"] = v, NA
                return out
        out["v"] = v
        if v is None:
            return out
        f_min, f_max = v - regra["minimo"], regra["maximo"] - v
        out["lim"] = f"{regra['minimo']:.2f}–{regra['maximo']:.2f}"
        out["folga"] = min(f_min, f_max)
        out["st"] = OK if out["folga"] >= -1e-9 else NAO
        return out

    if t == "teto_cota":
        if d.month not in regra["meses"]:
            out["st"] = NA
            return out
        v = dados.diaria(regra["fonte"]).get(iso)
        out["v"], out["lim"] = v, regra["maximo"]
        out["st"], out["folga"], out["pct"] = classificar(v, regra["maximo"], "max", None)
        return out

    if t in ("piso_horario", "teto_horario"):
        h = dados.horaria(regra["fonte"]).get(iso)
        if not h:
            out["obs"] = "sem dado horário"
            return out
        if t == "piso_horario":
            v, lim, sentido = min(h), regra["minimo"], "min"
        else:
            v, lim, sentido = max(h), regra["maximo"], "max"
        out["v"], out["lim"] = v, lim
        out["st"], out["folga"], out["pct"] = classificar(v, lim, sentido, None)
        out["obs"] = f"{len(h)} registros horários"
        return out

    if t == "volume_anual":
        serie = dados.diaria(regra["fonte"])
        if iso not in serie:
            return out
        ini = d.replace(month=1, day=1)
        vol = sum(max(serie.get(x.isoformat(), 0.0), 0.0) for x in dias(ini, d)) * 86400 / 1e6  # valores negativos = sentido inverso
        par = PARAMETROS[regra["param"]]
        lim = par.get("por_ano", {}).get(str(d.year), None) if "por_ano" in par else par["valor"]
        out["v"], out["lim"], out["obs"] = vol, lim, par.get("fonte_por_ano", {}).get(str(d.year), par["fonte"])
        if lim is None:
            out["st"], out["obs"] = PP, f"limite anual de {d.year} não informado (PARAMETROS['{regra['param']}']['por_ano'])"
            return out
        out["st"], out["folga"], out["pct"] = classificar(vol, lim, "max", None)
        return em_curso(out, d, "ano")  # avaliado no fechamento do ano (31/12)

    if t == "fds":
        if d.weekday() != 6:  # avaliado no domingo
            out["st"] = NA
            return out
        sexta = d - dt.timedelta(days=2)
        afl_d = dados.diaria("ONS:RIUHFC:afl")
        afl, n = media(afl_d, sexta, d)
        if n < 3:
            return out
        horas = dados.onsh.get("RIUHFC", {})
        janela = [v["defl"] for h, v in horas.items()
                  if f"{sexta.isoformat()} 12:00" <= h <= f"{d.isoformat()} 12:00" and v.get("defl") is not None]
        out["obs"] = f"afluência média sex–dom {afl:.0f} m³/s"
        if afl > regra["limite"]:
            out["st"] = NA
            out["obs"] += " (> 1.000: sem restrição)"
            return out
        if not janela:
            out["obs"] += "; sem dado horário"
            return out
        v = max(janela)
        out["v"], out["lim"] = v, regra["limite"]
        out["st"], out["folga"], out["pct"] = classificar(v, regra["limite"], "max", None)
        return out

    if t == "constante":
        a = dt.date(d.year, int(regra["inicio"][:2]), int(regra["inicio"][3:]))
        b = dt.date(d.year, int(regra["fim"][:2]), int(regra["fim"][3:]))
        if not (a <= d <= b):
            out["st"] = NA
            return out
        serie = dados.diaria(regra["fonte"])
        m, _ = media(serie, a, d)
        v = serie.get(iso)
        if v is None or m is None:
            return out
        out["v"], out["lim"] = v, f"{m * (1 - regra['tol']):.0f}–{m * (1 + regra['tol']):.0f}"
        out["folga"] = m * regra["tol"] - abs(v - m)
        out["st"] = OK if out["folga"] >= 0 else NAO
        return out

    if t == "vu_datas":
        alvo = [dt.date(d.year, int(x[:2]), int(x[3:])) for x in regra["datas"]]
        if d not in alvo:
            out["st"] = NA
            return out
        v = dados.diaria(regra["fonte"]).get(iso)
        out["v"], out["lim"] = v, regra["minimo"]
        out["st"], out["folga"], out["pct"] = classificar(v, regra["minimo"], "min", None)
        return out

    if t == "estagios":
        hist = faixas.estagios(regra)
        reg = hist.get(iso)
        if reg is None:
            return out
        estagio, vus = reg
        tol = regra["tol_pp"]
        aval, abaixo, acima = [], [], []
        for r, v in vus.items():
            lim = regra["fontes"][r][estagio]
            dev = v - lim  # desvio absoluto, em p.p. de VU
            st = OK if dev >= 0 else (TOL if dev >= -tol - 1e-9 else NAO)
            if -tol - 1e-9 <= dev < 0:
                abaixo.append(f"{NOME_ONS.get(r, r)} {dev:+.1f} p.p.")
            elif 0 <= dev <= tol + 1e-9:
                acima.append(f"{NOME_ONS.get(r, r)} {dev:+.1f} p.p.")
            aval.append((dev, st, r, v, lim))
        ordem = {OK: 0, TOL: 1, NAO: 2}
        dev, _, r, v, lim = min(aval)
        out["st"] = max((x[1] for x in aval), key=lambda s: ordem[s])
        out["v"], out["lim"], out["folga"], out["pct"] = v, lim, dev, (dev / lim * 100 if lim else None)
        if out["st"] == TOL:
            out["lado"] = "abaixo"
        elif acima:
            out["lado"] = "acima"
        out["faixa"], out["orig"] = f"{estagio + 1}º estágio", "inferido do histórico"
        partes = [f"menor folga: {NOME_ONS.get(r, r)}"]
        if abaixo:
            partes.append("tolerância para baixo: " + ", ".join(abaixo))
        if acima:
            partes.append("tolerância para cima: " + ", ".join(acima))
        partes.append(", ".join(f"{NOME_ONS.get(k, k)} {x:.1f}%" for k, x in vus.items()))
        out["obs"] = "; ".join(partes)
        return out

    if t == "movel":
        idx = com_faixa(regra["faixa"])
        if not umido(d, regra["umido"]):
            out["st"] = NA
            return out
        if idx is None:
            return out
        meta = regra["metas"][idx]
        if meta is None:
            out["st"] = NA
            return out
        serie = dados.diaria(regra["fonte"])
        v, n = media(serie, d - dt.timedelta(days=regra["dias"] - 1), d)
        if n < 10:
            return out
        out["v"], out["lim"] = v, meta
        out["st"], out["folga"], out["pct"] = classificar(v, meta, "min", None)
        return out

    if t == "media_periodo":
        a = dt.date(d.year, int(regra["inicio"][:2]), int(regra["inicio"][3:]))
        b = dt.date(d.year, int(regra["fim"][:2]), int(regra["fim"][3:]))
        if not (a <= d <= b):
            out["st"] = NA
            return out
        soma = dados.diaria("SOMA:" + "+".join(regra["fontes"]))
        v, n = media(soma, a, d)
        if v is None:
            return out
        out["v"], out["lim"] = v, regra["minimo"]
        if regra.get("informativa"):
            # garantia de disponibilidade (decisão da SOE em 04/11/2026): acompanha o volume, não gera descumprimento
            out["st"] = NA
            out["folga"], out["pct"] = round(v - regra["minimo"], 2), None
        else:
            out["st"], out["folga"], out["pct"] = classificar(v, regra["minimo"], "min", None)
        out["obs"] = f"{v * n * 86400 / 1e6:.1f} hm³ liberados em {n} dias"
        resta = (b - d).days
        if regra.get("informativa"):
            garantido = regra["minimo"] * ((b - a).days + 1) * 86400 / 1e6
            out["obs"] += f" de {garantido:.1f} hm³ garantidos; saldo disponível ao PCJ {garantido - v * n * 86400 / 1e6:.1f} hm³"
        elif resta > 0:
            total = (b - a).days + 1
            nec = (regra["minimo"] * total - v * n) / resta
            out["obs"] += f"; período em curso: média necessária nos {resta} dias restantes = {nec:.1f} m³/s"
        return out

    if t == "teto_cantareira":
        idx = com_faixa(regra["faixa"])
        serie = dados.diaria(regra["fonte"])
        if idx is None or iso not in serie:
            out["obs"] = "série de Santa Inês sem dado na API Mananciais da Sabesp (EEAB 36)"
            return out
        v, _ = media(serie, d.replace(day=1), d)
        lim = regra["limites"][idx]
        if 1 <= idx <= 3:
            tr, _ = media(dados.diaria(regra["soma_transf"]), d.replace(day=1), d)
            lim += tr or 0.0
        out["v"], out["lim"] = v, lim
        out["st"], out["folga"], out["pct"] = classificar(v, lim, "max", None)
        if fim_janela(d, "mes") > d:
            out["obs"] = "janela em curso"
        return em_curso(out, d, "mes")

    raise ValueError(f"tipo desconhecido: {t}")


NOME_ONS = {"PBFUNI": "Funil", "PBSTAB": "Santa Branca", "PBPARA": "Paraibuna", "PBJAGU": "Jaguari"}
FAIXAS_NOME = {"GRFURN": "Furnas", "PRITUM": "Itumbiara", "SFSOBR": "Sobradinho"}


# ============================================================================ execução
def _r(x, n=2):
    return round(x, n) if isinstance(x, float) else x


def parametro(regra) -> str:
    """Nome do parâmetro apurado (grandeza + agregação), exibido junto do valor no painel."""
    t, fonte = regra["tipo"], regra.get("fonte")
    campo = str(fonte or "").split(":")[-1] if isinstance(fonte, str) else ""
    rid = regra["id"]
    if t == "estagios":
        return "Volume útil dos reservatórios do Paraíba do Sul (menor margem ao mínimo do estágio)"
    if t == "media_periodo":
        return "Vazão liberada ao PCJ (Jaguari-Jacareí + Cachoeira + Atibainha), média desde 1º/jun"
    if t == "volume_anual":
        return "Volume captado na EEAB Jaguari (Sabesp), acumulado desde 1º/jan"
    if t == "teto_cantareira":
        return "Vazão captada na EESI (Santa Inês), média do mês"
    if t == "fds":
        return "Vazão defluente horária, sex 12h a dom 12h"
    if str(fonte).startswith("SAISP:"):
        base = "Vazão no posto fluviométrico"
    elif campo == "defl":
        base = "Descarga para jusante" if str(fonte).startswith(("SAR:", "SOMA:SAR")) else "Vazão defluente"
    elif str(fonte).startswith("SABESP:jaguari_ps"):
        base = ("Vazão captada no Atibainha (sentido inverso, EEAB Jaguari/Sabesp), dia de 07h a 07h" if regra.get("sentido") == "inverso"
                else "Vazão captada na EEAB Jaguari (Sabesp), dia de 07h a 07h")
    elif campo == "transf":
        base = "Vazão bombeada ao Guandu" if "PBSTAC" in rid else "Vazão captada na interligação Jaguari–Atibainha"
    elif campo == "vu":
        base = "Volume útil"
    elif campo in ("nivel", "cota"):
        base = "Nível d'água (cota)"
    elif campo == "afl":
        base = "Vazão afluente"
    else:
        base = "Valor"
    agreg = {"piso_horario": "mínimo horário do dia", "teto_horario": "máximo horário do dia", "movel": "média móvel de 15 dias",
             "constante": "média diária", "piso": "média diária", "teto_diario": "média diária",
             "vu_min": "diário", "vu_datas": "na data de verificação", "intervalo": "diário", "intervalo_se": "diário",
             "teto_cota": "diário"}.get(t)
    if t == "teto":
        agreg = AGREG_JANELA.get(regra.get("janela", "dia"), "média diária")
    return f"{base}, {agreg}" if agreg else base


AGREG_JANELA = {"dia": "média diária", "semana": "média da semana operativa (sáb–sex)", "mes": "média do mês"}


def unidade(regra) -> str:
    campo = str(regra.get("fonte", "")).split(":")[-1]
    if regra["tipo"] == "volume_anual":
        return "hm³"
    if regra["tipo"] in ("vu_min", "vu_datas", "estagios") or campo in ("vu",):
        return "%"
    if regra["tipo"] in ("teto_cota",) or campo in ("cota", "nivel"):
        return "m"
    return "m³/s"


def executar(inicio: dt.date, fim: dt.date, meses_horarios: int) -> dict:
    dados = Dados(inicio, fim, meses_horarios)
    faixas = Faixas(dados)
    ultimo_ons = max((max(v) for v in dados.ons.values() if v), default=None)
    saida = {"gerado_em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "periodo": [inicio.isoformat(), fim.isoformat()], "ultimo_dado_ons": ultimo_ons,
             "erros_coleta": dados.erros, "parametros": PARAMETROS, "regras": []}
    for regra in REGRAS:
        serie = []
        for d in dias(inicio, fim):
            try:
                a = avaliar(regra, dados, faixas, d)
            except Exception as e:  # uma regra com problema não derruba o painel
                a = {"d": d.isoformat(), "st": SD, "obs": f"erro: {e}"}
            if a.get("st") == NAO and regra.get("alerta"):
                a["obs"] = "; ".join(x for x in (a.get("obs"), "verificar se a redução foi justificada") if x)
            serie.append({k: _r(v) for k, v in a.items() if v is not None or k in ("d", "st")})
        # último registro avaliado (ignora dias sem dado no fim da série)
        validos = [s for s in serie if s["st"] not in (SD,)]
        ult = validos[-1] if validos else serie[-1]
        mensal = defaultdict(lambda: defaultdict(int))
        for s in serie:
            if s["st"] in (OK, TOL, NAO):
                mensal[s["d"][:7]][s["st"]] += 1
                mensal[s["d"][:7]]["n"] += 1
        meta = {k: v for k, v in regra.items() if k in ("id", "resolucao", "artigos", "sistema", "ponto", "descricao", "freq", "niveis", "tol", "tol_pp", "fonte", "tipo", "alerta")}
        meta["unid"] = unidade(regra)
        meta["param"] = parametro(regra)
        serie = serie[-DIAS_SERIE:]
        # Dias anteriores à vigência saem da série (o painel os recompõe como "Não se aplica"): reduz o JSON.
        n_pre = 0
        while n_pre < len(serie) and serie[n_pre]["st"] == NA and str(serie[n_pre].get("obs", "")).startswith("antes d"):
            n_pre += 1
        pre = {"ate": serie[n_pre]["d"] if n_pre < len(serie) else None, "obs": serie[0].get("obs")} if n_pre else None
        saida["regras"].append({**meta, "ultimo": ult, "mensal": {k: dict(v) for k, v in sorted(mensal.items())},
                                **({"pre_vigencia": pre} if pre else {}), "serie": serie[n_pre:]})
    saida["faixas"] = []
    for rid in FAIXAS:
        nome, idx, orig, ref = faixas.de(rid, fim)
        per, _ = faixas.chave_periodo(rid, fim)
        pbi = dados.faixas_pbi.get(f"{rid}|{per}") or {}
        saida["faixas"].append({"rid": rid, "periodo": per, "faixa": nome, "idx": idx, "origem": orig, "vu_ref": ref,
                                "limites": pbi.get("limites") if isinstance(pbi, dict) else None,
                                "url": pbi.get("url") if isinstance(pbi, dict) else None})
    saida["divergencias_faixa"] = [{"chave": f"{k[0]}|{k[1]}", **v} for k, v in faixas.divergencias.items()]
    return saida


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    hoje = dt.date.today()
    ap.add_argument("--inicio", default="2017-10-01")  # início do histórico de faixas dos boletins (Cantareira)
    ap.add_argument("--fim", default=hoje.isoformat())
    ap.add_argument("--meses-horarios", type=int, default=0)  # 0 = dado horário desde --inicio
    ap.add_argument("--saida", default=str(SAIDA))
    a = ap.parse_args()
    res = executar(dt.date.fromisoformat(a.inicio), dt.date.fromisoformat(a.fim), a.meses_horarios)
    Path(a.saida).parent.mkdir(parents=True, exist_ok=True)
    Path(a.saida).write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    cont = defaultdict(int)
    for r in res["regras"]:
        cont[r["ultimo"]["st"]] += 1
    print(f"[ok] {len(res['regras'])} regras -> {a.saida}  {dict(cont)}")
    for e in res["erros_coleta"]:
        print(f"[aviso] {e}")


if __name__ == "__main__":
    main()
