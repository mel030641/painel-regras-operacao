"""atos.py - Cadastro único dos atos complementares às resoluções (config/atos_complementares.json).

Um ato complementar é um documento fora da resolução que fixa ou altera o limite ou o gatilho de uma regra:
outorga, comunicado, ofício de manifestação sobre curvas, valor mensal fixado ou situação declarada pelo operador.
Este módulo lê o cadastro, confere a consistência dos registros e entrega os valores no formato usado por
src/regras.py e src/calculo.py.

Uso: python src/atos.py   (confere o cadastro e lista os atos por regra)
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent.parent / "config" / "atos_complementares.json"

TIPOS = {"outorga", "comunicado", "oficio", "confirmacao", "registro", "valor_mensal", "situacao_declarada", "situacao_identificada"}
EFEITOS = {"parametro", "revisao_estagio", "tolerancia", "curva_seguranca", "valores_mensais", "restricao_mensal",
           "vazao_temporada", "previsao_semanal", "situacao"}
MENSAIS = ("valores_mensais", "restricao_mensal")  # mesmo formato: {parametro, valores {aaaa-mm: valor}}
MES = re.compile(r"^\d{4}-\d{2}$")
RASTREIO = ("cadastrado_por", "cadastrado_em", "revisado_por")  # opcionais; preenchidos pelo formulário ou à mão
DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CadastroInvalido(ValueError):
    pass


def _conferir(atos: list[dict]) -> None:
    erros, ids = [], set()
    for i, a in enumerate(atos):
        rot = a.get("id") or f"ato nº {i + 1}"
        for campo in ("id", "tipo", "documento", "regras", "vigencia", "citacao", "efeito"):
            if campo not in a:
                erros.append(f"{rot}: falta o campo '{campo}'")
        if a.get("id") in ids:
            erros.append(f"{rot}: id repetido")
        ids.add(a.get("id"))
        if a.get("tipo") not in TIPOS:
            erros.append(f"{rot}: tipo '{a.get('tipo')}' fora de {sorted(TIPOS)}")
        ef = a.get("efeito") or {}
        if not EFEITOS & set(ef):
            erros.append(f"{rot}: efeito sem nenhum de {sorted(EFEITOS)}")
        if "parametro" in ef and "valor" not in ef:
            erros.append(f"{rot}: efeito 'parametro' sem 'valor'")
        vig = a.get("vigencia") or {}
        for k in ("inicio", "fim"):
            if vig.get(k) is not None and not DATA.match(vig[k]):
                erros.append(f"{rot}: vigencia.{k} '{vig[k]}' fora do formato aaaa-mm-dd")
        if vig.get("inicio") and vig.get("fim") and vig["fim"] < vig["inicio"]:
            erros.append(f"{rot}: vigência termina antes de começar")
        if a.get("data") is not None and not DATA.match(str(a["data"])):
            erros.append(f"{rot}: data '{a['data']}' fora do formato aaaa-mm-dd")
        for k in MENSAIS:
            if k in ef:
                m = ef[k]
                if not isinstance(m, dict) or not m.get("parametro") or not isinstance(m.get("valores"), dict) or not m["valores"]:
                    erros.append(f"{rot}: efeito '{k}' precisa de parametro e valores {{aaaa-mm: valor}}")
                else:
                    for mes, v in m["valores"].items():
                        if not MES.match(str(mes)):
                            erros.append(f"{rot}: {k}: mês '{mes}' fora do formato aaaa-mm")
                        if not isinstance(v, (int, float)):
                            erros.append(f"{rot}: {k}: valor de {mes} não numérico")
        if "vazao_temporada" in ef:
            t = ef["vazao_temporada"]
            if not isinstance(t, dict) or not t.get("reservatorio") or not re.match(r"^\d{4}$", str(t.get("ano", ""))) or not isinstance(t.get("valor"), (int, float)):
                erros.append(f"{rot}: efeito 'vazao_temporada' precisa de reservatorio, ano (aaaa) e valor numérico")
        if "previsao_semanal" in ef:
            pv = ef["previsao_semanal"]
            if not isinstance(pv, dict) or not pv.get("reservatorio") or not isinstance(pv.get("valores"), dict) or not pv["valores"]:
                erros.append(f"{rot}: efeito 'previsao_semanal' precisa de reservatorio e valores {{aaaa-mm-dd da sexta: m³/s}}")
            else:
                import datetime as _dt
                for dia, v in pv["valores"].items():
                    if not DATA.match(str(dia)) or _dt.date.fromisoformat(dia).weekday() != 4:
                        erros.append(f"{rot}: previsao_semanal: '{dia}' não é uma sexta-feira no formato aaaa-mm-dd")
                    if not isinstance(v, (int, float)):
                        erros.append(f"{rot}: previsao_semanal: valor de {dia} não numérico")
        if a.get("cadastrado_em") is not None and not DATA.match(str(a["cadastrado_em"])[:10]):
            erros.append(f"{rot}: cadastrado_em '{a['cadastrado_em']}' fora do formato aaaa-mm-dd")
        if "revisao_estagio" in ef and not a["vigencia"].get("fim"):
            erros.append(f"{rot}: revisão de estágio precisa de vigência com fim")
    if erros:
        raise CadastroInvalido("config/atos_complementares.json:\n  " + "\n  ".join(erros))


@lru_cache(maxsize=None)
def _carregar(caminho: str) -> tuple:
    p = Path(caminho)
    if not p.exists():
        return ()
    atos = json.loads(p.read_text(encoding="utf-8")).get("atos", [])
    _conferir(atos)
    return tuple(atos)


def todos(caminho: Path = ARQUIVO) -> list[dict]:
    return [dict(a) for a in _carregar(str(caminho))]


def _com(efeito: str) -> list[dict]:
    return [a for a in todos() if efeito in a["efeito"]]


def parametros(base: dict) -> dict:
    """PARAMETROS de src/regras.py com os valores dos atos: limite fixo (valor/fonte) ou limite por ano (por_ano/fonte_por_ano)."""
    out = {k: dict(v) for k, v in base.items()}
    for a in _com("parametro"):
        ef = a["efeito"]
        p = out.setdefault(ef["parametro"], {})
        if "ano" in ef:
            p.setdefault("por_ano", {})[ef["ano"]] = ef["valor"]
            p.setdefault("fonte_por_ano", {})[ef["ano"]] = a["citacao"]
        else:
            p["valor"], p["fonte"] = ef["valor"], a["citacao"]
    for p in out.values():  # ordem estável por ano
        for k in ("por_ano", "fonte_por_ano"):
            if k in p:
                p[k] = dict(sorted(p[k].items()))
    return out


def revisoes_estagio(regra_id: str) -> list[dict]:
    """Revisões temporárias dos mínimos dos estágios (1.382/2015, art. 1º, § 2º), no formato da regra 1382-estagios."""
    out = []
    for a in _com("revisao_estagio"):
        if regra_id in a["regras"]:
            r = a["efeito"]["revisao_estagio"]
            out.append(dict(fonte=r["reservatorio"], estagio=r["estagio"], valor=r["valor"],
                            inicio=a["vigencia"]["inicio"], fim=a["vigencia"]["fim"], ato=a["citacao"]))
    return sorted(out, key=lambda r: r["inicio"])


def tolerancia(regra_id: str) -> dict | None:
    """Tolerância admitida por ato para a regra: {'tol', 'tol_desde', 'tol_ato'}."""
    for a in _com("tolerancia"):
        if regra_id in a["regras"]:
            return {"tol": a["efeito"]["tolerancia"], "tol_desde": a["vigencia"]["inicio"], "tol_ato": a["citacao"]}
    return None


def manifestacoes_curvas() -> dict[str, str]:
    """{ciclo: texto da manifestação da ANA} para as curvas de segurança do São Francisco (2.081/2017, art. 4º)."""
    return {a["efeito"]["curva_seguranca"]: a["citacao"] for a in _com("curva_seguranca")}


def _mensais():
    for k in MENSAIS:
        for a in _com(k):
            yield a, a["efeito"][k]


def valores_mensais() -> dict[str, float]:
    """{'<parâmetro>|aaaa-mm': valor} dos valores mensais fixados por ato: tetos da 2.081/2017, nível operacional
    da 614/2010 (valores_mensais) e vazão de restrição mensal da 614/2010 (restricao_mensal)."""
    out = {}
    for _, ef in _mensais():
        for mes, v in ef["valores"].items():
            out[f"{ef['parametro']}|{mes}"] = v
    return out


def citacao_mensal(chave: str) -> str | None:
    """Texto do ato que fixou o valor mensal '<parâmetro>|aaaa-mm'."""
    par, mes = chave.split("|")
    for a, ef in _mensais():
        if ef["parametro"] == par and mes in ef["valores"]:
            return a["citacao"]
    return None


def previsoes() -> dict[str, dict]:
    """{'<reservatório>|aaaa-mm-dd (sexta)': {'valor', 'citacao'}} da previsão de afluência sex–dom do ONS (Res. ANA 49/2018, art. 1º, § 1º)."""
    out = {}
    for a in _com("previsao_semanal"):
        pv = a["efeito"]["previsao_semanal"]
        for dia, v in pv["valores"].items():
            out[f"{pv['reservatorio']}|{dia}"] = {"valor": v, "citacao": a["citacao"]}
    return out


def vazoes_temporada() -> dict[str, dict]:
    """{'<reservatório>|aaaa': {'valor', 'citacao'}} da vazão da temporada informada por ato (Res. ANA 70/2021, art. 6º, § 3º)."""
    out = {}
    for a in _com("vazao_temporada"):
        t = a["efeito"]["vazao_temporada"]
        out[f"{t['reservatorio']}|{t['ano']}"] = {"valor": t["valor"], "citacao": a["citacao"]}
    return out


def situacoes() -> list[dict]:
    """Situações declaradas ou identificadas (só observação; não mudam a avaliação), no formato usado por calculo.obs_eventos."""
    return [{"id": a["id"], "sistema": a["efeito"]["situacao"], "inicio": a["vigencia"]["inicio"], "fim": a["vigencia"].get("fim"),
             "regras": a["regras"], "obs": a["citacao"], "fonte": a["documento"]} for a in _com("situacao")]


def _num(v: float, casas: int | None = None) -> str:
    """Número no padrão brasileiro (vírgula decimal, ponto de milhar)."""
    t = f"{v:,.{2 if casas is None else casas}f}"
    if casas is None and "." in t:
        t = t.rstrip("0").rstrip(".")
    return t.replace(",", "§").replace(".", ",").replace("§", ".")


MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
_mes = lambda m: f"{MESES[int(m[5:7]) - 1]}/{m[:4]}"  # noqa: E731  ('2026-12' -> 'dez/2026')


def efeito_txt(a: dict) -> str:
    """Efeito do ato em uma frase curta, para a lista de atos do painel."""
    ef = a["efeito"]
    if "parametro" in ef:
        p = ef["parametro"]
        if p.startswith("outorga_"):
            return f"vazão outorgada de {_num(ef['valor'])} m³/s"
        if p == "vol_anual_jaguari":
            return f"volume anual de {ef.get('ano', '')}: {_num(ef['valor'])} hm³".replace(" :", ":")
        return f"{p} = {_num(ef['valor'])}" + (f" ({ef['ano']})" if "ano" in ef else "")
    if "revisao_estagio" in ef:
        r = ef["revisao_estagio"]
        return f"mínimo do estágio {r['estagio']} em {r['reservatorio']}: {_num(r['valor'])}% VU"
    if "tolerancia" in ef:
        return f"tolerância de {_num(ef['tolerancia'] * 100)}%"
    if "curva_seguranca" in ef:
        return f"curvas de segurança do ciclo {ef['curva_seguranca']}"
    for k in MENSAIS:
        if k in ef:
            v = ef[k]
            meses = sorted(v["valores"])
            nome = "vazão de restrição" if k == "restricao_mensal" else v["parametro"]
            if len(set(v["valores"].values())) == 1:
                un = " m³/s" if k == "restricao_mensal" else ""
                return f"{nome}: {_num(next(iter(v['valores'].values())))}{un} de {_mes(meses[0])} a {_mes(meses[-1])}"
            return f"{nome}: {len(meses)} valores mensais, {_mes(meses[0])} a {_mes(meses[-1])}"
    if "previsao_semanal" in ef:
        pv = ef["previsao_semanal"]
        dias = sorted(pv["valores"])
        return f"previsão de afluência sex–dom em {pv['reservatorio']}: {len(dias)} semana(s), {dias[0][8:10]}/{dias[0][5:7]}/{dias[0][:4]} a {dias[-1][8:10]}/{dias[-1][5:7]}/{dias[-1][:4]}"
    if "vazao_temporada" in ef:
        t = ef["vazao_temporada"]
        return f"vazão da temporada {t['ano']} em {t['reservatorio']}: {_num(t['valor'])} m³/s"
    if "situacao" in ef:
        quem = "identificada nos dados" if a.get("tipo") == "situacao_identificada" else "declarada"
        return f"situação {quem} ({ef['situacao']}); só observação"
    return ", ".join(sorted(ef))


def resumo() -> list[dict]:
    """Lista enxuta dos atos para o painel (aba de atos complementares)."""
    campos = ("id", "tipo", "documento", "data", "sei", "regras", "vigencia", "citacao") + RASTREIO
    return [{**{k: a.get(k) for k in campos}, "efeito_txt": efeito_txt(a)} for a in todos()]


def cobre(a: dict, inicio: str, fim: str) -> bool:
    """O ato está vigente em algum dia de [inicio, fim] (datas aaaa-mm-dd; vigência sem início ou sem fim é aberta)."""
    v = a.get("vigencia") or {}
    return (v.get("inicio") or "0000-01-01") <= fim and (v.get("fim") or "9999-12-31") >= inicio



def cobertura(regra_id: str, serie: list[dict], pendente: str = "Parâmetro pendente", nao_aplica: str = "Não se aplica") -> dict[str, str]:
    """Cobertura mensal da regra pelo cadastro de atos, a partir da série diária já avaliada.

    'lacuna': algum dia do mês ficou em Parâmetro pendente (falta o ato);
    'ato':    há ato do cadastro vigente no mês para a regra;
    'sem':    o mês não exige ato (regra não se aplica ou o limite vem da própria resolução).
    """
    meses: dict[str, list[str]] = {}
    for s in serie:
        meses.setdefault(s["d"][:7], []).append(s["st"])
    atos_r = [a for a in todos() if regra_id in a["regras"] and not ({"situacao"} & set(a["efeito"]))]
    out = {}
    for m, sts in meses.items():
        a0, a1 = m + "-01", m + "-31"
        if pendente in sts:
            out[m] = "lacuna"
        elif all(st == nao_aplica for st in sts):
            out[m] = "sem"
        elif any(cobre(a, a0, a1) for a in atos_r):
            out[m] = "ato"
        else:
            out[m] = "sem"
    return out


# --------------------------------------------------------------------------- próximo ato esperado
STATUS_PROXIMO = ("em_dia", "aguardando", "atrasado", "eventual", "sem")
JANELA_AGUARDANDO = 30  # dias antes da data-limite em que o ato passa a "aguardando"


def _d(s):
    import datetime as _dt
    return _dt.date.fromisoformat(s)


def _status(prazo, hoje, atendido: bool, janela: int = JANELA_AGUARDANDO) -> str:
    import datetime as _dt
    if atendido:
        return "em_dia"
    if prazo < hoje:
        return "atrasado"
    return "aguardando" if prazo <= hoje + _dt.timedelta(days=janela) else "em_dia"


def _atos_limite(regra_id: str) -> list[dict]:
    return [a for a in todos() if regra_id in a["regras"] and "situacao" not in a["efeito"]]


def proximo(regra_id: str, spec: dict, hoje) -> dict:
    """Próximo ato esperado da regra, na data de referência 'hoje' (datetime.date).

    spec (src/regras.py, PROXIMO_ATO):
      tipo 'ciclo':      ato que vale de 'inicio' a 'fim' (aaaa omitido, MM-DD) a cada ano, com data-limite 'prazo' (MM-DD)
                         no mesmo ano do início; o ciclo está atendido quando há ato da regra vigente no início do ciclo
                         ou ato com o ano do ciclo (vazao_temporada.ano ou efeito.ano);
      tipo 'semanal':    previsão sex–dom do ONS, obtida na quinta (previsao_semanal);
      tipo 'vencimento': renovação do ato vigente (outorga), esperada até o fim da vigência; 'antecedencia' em dias;
      tipo 'eventual':   só quando ocorre o fato descrito em 'quando';
      tipo 'sem':        não há ato a esperar ('motivo').
    Devolve {tipo, periodicidade, fonte, base, alvo, prazo (aaaa-mm-dd ou None), status, ato}.
    """
    import datetime as _dt
    base = {k: spec.get(k) for k in ("periodicidade", "fonte", "base")}
    tipo = spec["tipo"]
    atos_r = _atos_limite(regra_id)
    if tipo in ("eventual", "sem"):
        return {**base, "tipo": tipo, "alvo": spec.get("quando") or spec.get("motivo"), "prazo": None, "status": tipo, "ato": None}
    if tipo == "semanal":
        sexta = hoje + _dt.timedelta(days=(4 - hoje.weekday()) % 7) if hoje.weekday() <= 4 else hoje - _dt.timedelta(days=hoje.weekday() - 4)
        reg = previsoes()
        res = spec["reservatorio"]
        while True:
            chave = f"{res}|{sexta.isoformat()}"
            prazo = sexta - _dt.timedelta(days=1)
            if chave in reg and prazo <= hoje + _dt.timedelta(days=7):
                sexta += _dt.timedelta(days=7)
                continue
            st = _status(prazo, hoje, chave in reg, janela=7)
            return {**base, "tipo": tipo, "alvo": f"previsão para sex–dom de {sexta.strftime('%d/%m/%Y')}", "prazo": prazo.isoformat(),
                    "status": st, "ato": None}
    if tipo == "vencimento":
        vig = [a for a in atos_r if cobre(a, hoje.isoformat(), hoje.isoformat())]
        if not vig:
            return {**base, "tipo": tipo, "alvo": "ato vigente (nenhum no cadastro)", "prazo": hoje.isoformat(), "status": "atrasado", "ato": None}
        a = max(vig, key=lambda x: x["vigencia"].get("fim") or "9999-12-31")
        fim = a["vigencia"].get("fim")
        if not fim:
            return {**base, "tipo": tipo, "alvo": "renovação (vigência sem prazo no cadastro)", "prazo": None, "status": "em_dia", "ato": a["documento"]}
        prazo = _d(fim)
        renov = [b for b in atos_r if b is not a and cobre(b, (prazo + _dt.timedelta(days=1)).isoformat(), (prazo + _dt.timedelta(days=1)).isoformat())]
        st = _status(prazo, hoje, bool(renov), janela=spec.get("antecedencia", 180))
        return {**base, "tipo": tipo, "alvo": f"renovação de {a['documento']}", "prazo": fim, "status": st, "ato": a["documento"]}
    if tipo == "ciclo":
        def janela(ano):
            ini = _d(f"{ano}-{spec['inicio']}")
            fim = _d(f"{ano + (1 if spec['fim'] < spec['inicio'] else 0)}-{spec['fim']}")
            return ini, fim, _d(f"{ano}-{spec['prazo']}")

        def atendido(ini):
            for a in atos_r:
                ef = a["efeito"]
                ano = (ef.get("vazao_temporada") or {}).get("ano", ef.get("ano"))
                if cobre(a, ini.isoformat(), ini.isoformat()) or (ano is not None and str(ano) == str(ini.year)):
                    return a
            return None

        ano = hoje.year - 1
        while True:
            ini, fim, prazo = janela(ano)
            if fim >= hoje:
                a = atendido(ini)
                if not a or prazo > hoje + _dt.timedelta(days=JANELA_AGUARDANDO) or ano > hoje.year + 1:
                    alvo = f"{spec.get('rotulo', 'ciclo')} {ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}"
                    return {**base, "tipo": tipo, "alvo": alvo, "prazo": prazo.isoformat(), "status": _status(prazo, hoje, bool(a)),
                            "ato": a["documento"] if a else None}
            ano += 1
    raise ValueError(f"{regra_id}: tipo de próximo ato desconhecido: {tipo}")


if __name__ == "__main__":
    import sys
    # --validar: só confere o cadastro e as regras citadas (usado na validação dos pedidos de cadastro); sai com 1 se houver erro
    validar = "--validar" in sys.argv[1:]
    sys.path.insert(0, str(Path(__file__).parent))
    import regras
    ids = {r["id"] for r in regras.REGRAS}
    try:
        atos = todos()
    except CadastroInvalido as e:
        print(f"[erro] {e}")
        sys.exit(1)
    sem_regra = [(a["id"], r) for a in atos for r in a["regras"] if r not in ids]
    print(f"{len(atos)} atos em {ARQUIVO.name}")
    for a in atos if not validar else []:
        v = a["vigencia"]
        print(f"  {a['id']:<28} {a['tipo']:<18} {v.get('inicio') or '—'} a {v.get('fim') or '—'}  {', '.join(a['regras'])}")
    if sem_regra:
        print("Regras citadas que não existem em src/regras.py:", sem_regra)
        sys.exit(1)
    if validar:
        print("[ok] cadastro válido")
