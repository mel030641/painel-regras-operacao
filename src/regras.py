"""
regras.py - Catálogo das regras auditáveis (validadas em 03/11/2026 contra o texto integral no gov.br).

Cada regra é um dicionário com:
  id, resolucao, artigos, sistema, ponto  -> identificação (aparecem no painel)
  tipo                                    -> algoritmo em calculo.py
  freq                                    -> diaria | semanal | mensal | anual | periodo
  niveis                                  -> 3 (Atende/Tolerância/Não atende) quando o ato prevê tolerância; 2 caso contrário
  tol                                     -> tolerância relativa (0.05 = 5%); None quando não há
  fonte                                   -> chave da série (ver calculo.SERIES)
  demais campos                           -> parâmetros específicos do tipo

Faixas: o painel usa a faixa publicada no Power BI (config/faixas.json). Enquanto o arquivo não tiver a faixa
do período, a faixa é calculada pela regra do ato e marcada como "calculada".

Unidades: vazão m³/s, cota m, volume % do volume útil (VU) ou hm³.
"""

# Períodos hidrológicos (meses do período úmido)
UMIDO_DEZ_ABR = [12, 1, 2, 3, 4]          # 193/2024, 194/2024, 2.081/2017
UMIDO_DEZ_MAI = [12, 1, 2, 3, 4, 5]       # 70/2021, 925/2017
UMIDO_NOV_ABR = [11, 12, 1, 2, 3, 4]      # 147/2015

# Definição das faixas por reservatório: variável, limiares (decrescentes) e nomes das faixas
FAIXAS = {
    # 132/2022, arts. 2º, 4º, 6º e 8º (cota; faixa da sexta-feira anterior à semana operativa)
    "PPJURU": {"var": "nivel", "limiares": [563.35, 562.48, 562.04], "nomes": ["Normal", "Atenção", "Alerta", "Restrição"], "definicao": "semana_sexta"},
    "PPCHAV": {"var": "nivel", "limiares": [469.06, 468.15, 467.21], "nomes": ["Normal", "Atenção", "Alerta", "Restrição"], "definicao": "semana_sexta"},
    "PPCAPI": {"var": "nivel", "limiares": [327.11, 324.27, 323.50], "nomes": ["Normal", "Atenção", "Alerta", "Restrição"], "definicao": "semana_sexta"},
    # 193/2024, arts. 3º, 6º e 10 (VU no dia 1º do mês)
    "GRFURN": {"var": "vu", "limiares": [50, 20], "nomes": ["Normal", "Atenção", "Restrição"], "definicao": "mes_dia1"},
    "GRMMOR": {"var": "vu", "limiares": [70, 30], "nomes": ["Normal", "Atenção", "Restrição"], "definicao": "mes_dia1"},
    # 194/2024, arts. 3º, 6º e 10
    "PREMBO": {"var": "vu", "limiares": [50, 20], "nomes": ["Normal", "Atenção", "Restrição"], "definicao": "mes_dia1"},
    "PRITUM": {"var": "vu", "limiares": [40, 20], "nomes": ["Normal", "Atenção", "Restrição"], "definicao": "mes_dia1"},
    # 2.081/2017, arts. 5º e 9º (início de cada mês; volume do dia anterior)
    "SFTMAR": {"var": "vu", "limiares": [60, 30], "nomes": ["Normal", "Atenção", "Restrição"], "definicao": "mes_dia_anterior"},
    "SFSOBR": {"var": "vu", "limiares": [60, 20], "nomes": ["Normal", "Atenção", "Restrição"], "definicao": "mes_dia_anterior"},
    # 70/2021, arts. 4º e 5º (cota; Alerta só no período seco)
    "TOSMES": {"var": "nivel", "limiares": [431.67, 425.41], "nomes": ["Normal", "Atenção", "Alerta"], "definicao": "mes_dia1",
               "umido": UMIDO_DEZ_MAI, "limiares_umido": [431.67], "nomes_umido": ["Normal", "Atenção"]},
    # 925/2017, arts. 4º e 6º (VU do sistema; ANA/DAEE até o último dia útil do mês anterior)
    "CANTAREIRA": {"var": "vu", "limiares": [60, 40, 30, 20], "nomes": ["Faixa 1 - Normal", "Faixa 2 - Atenção", "Faixa 3 - Alerta", "Faixa 4 - Restrição", "Faixa 5 - Especial"], "definicao": "mes_dia_anterior"},
}

# Parâmetros externos à resolução (outorgas, comunicados). Fonte registrada para auditoria.
PARAMETROS = {
    "outorga_GRFURN": {"valor": 1440.0, "fonte": "Outorga nº 1033/2019 (planilha de auditoria SOE, out/2026)"},
    "outorga_PREMBO": {"valor": 972.0, "fonte": "Outorga (planilha de auditoria SOE, out/2026)"},
    "outorga_PRITUM": {"valor": 2928.0, "fonte": "Outorga (planilha de auditoria SOE, out/2026)"},
    "outorga_GRMMOR": {"valor": 1188.64, "fonte": "Outorga ANA nº 2.704/2024, art. 1º, IX: vazão máxima turbinada de M. Mascarenhas de Moraes (efeitos desde 01/10/2024)"},
    "vol_anual_jaguari": {"valor": 268.28, "por_ano": {"2024": 162.0, "2025": 186.7, "2026": 268.28},
                          "fonte_por_ano": {"2024": "Res. ANA 1.931/2017, art. 1º, III (sem comunicado em 2024, confirmado pela SOE em 04/11/2026)",
                                            "2025": "Comunicado Conjunto ANA/SP-Águas/IGAM/INEA de 2025, item I.a (162 + 24,7 hm³ suplementares, até 31/12/2025)",
                                            "2026": "Comunicado Conjunto nº 3/2026 (planilha de auditoria SOE)"},
                          "fonte": "Res. ANA 1.931/2017, art. 1º, III: 162 hm³, salvo comunicado"},
}

# Início de vigência de cada resolução (texto oficial em normas/textos). Antes dessas datas a regra é
# registrada como "Não se aplica". O painel cobre desde out/2017 (início do histórico de faixas dos boletins).
VIGENCIA = {
    "Res. Conj. ANA/DAEE 614/2010": "2010-11-23",  # art. 7º (vigor na publicação, DOU 23/11/2010)
    "Res. ANA 147/2015": "2015-03-05",             # art. 2º (vigor na publicação, DOU 05/03/2015)
    "Res. Conj. ANA/DAEE 925/2017": "2017-05-30",  # art. 9º (vigor na publicação, DOU 30/05/2017)
    "Res. ANA 1.931/2017": "2017-11-01",           # art. 2º (vigor na publicação, DOU 01/11/2017)
    "Res. ANA 49/2018": "2018-07-18",              # art. 3º (vigor na publicação, DOU 18/07/2018)
    "Res. ANA 70/2021": "2021-12-01",              # art. 18
    "Res. ANA 132/2022": "2023-01-01",             # art. 20
    "Res. ANA 193/2024": "2024-12-02",             # art. 22
    "Res. ANA 194/2024": "2024-12-02",             # art. 22
}

# Resoluções que entram em vigor "após a emissão de comunicado pela ANA", sem a data do comunicado
# registrada aqui. Até a data ser conferida, a avaliação começa na data indicada (início original do painel).
VIGENCIA_A_CONFERIR = {
    "Res. Conj. ANA/DAEE/IGAM/INEA 1.382/2015": "2024-01-01",  # art. 7º: vigor após comunicado da ANA
    "Res. ANA 2.081/2017": "2024-01-01",                       # art. 22: vigor após comunicado da ANA
}

R = []  # lista de regras

# --------------------------------------------------------------------------- 132/2022 Paranapanema
for rid, nome, art, tetos in [
    ("PPJURU", "Jurumirim", "arts. 2º, 3º, 8º e 10", [None, 182, 147, 90]),
    ("PPCHAV", "Chavantes", "arts. 4º, 5º, 8º e 10", [None, 322, 161, 127]),
    ("PPCAPI", "Capivara", "arts. 6º, 7º, 8º e 10", [None, 990, 730, 405]),
]:
    R.append(dict(id=f"132-{rid}", resolucao="Res. ANA 132/2022", artigos=art, sistema="Paranapanema", ponto=nome,
                  descricao="Defluente média semanal (sáb–sex) ≤ teto da faixa", tipo="teto", freq="semanal", niveis=3, tol=0.05,
                  fonte=f"ONS:{rid}:defl", faixa=rid, janela="semana", tetos={"todos": tetos}))

# --------------------------------------------------------------------------- 193/2024 Rio Grande
R.append(dict(id="193-GRFURN", resolucao="Res. ANA 193/2024", artigos="arts. 2º a 5º, 10 e 11", sistema="Rio Grande", ponto="Furnas",
              descricao="Defluente ≤ teto da faixa e do período", tipo="teto", freq="mensal", niveis=3, tol=0.05,
              fonte="ONS:GRFURN:defl", faixa="GRFURN", janela="mes", umido=UMIDO_DEZ_ABR,
              tetos={"umido": [None, 500, 400], "seco": [{"param": "outorga_GRFURN", "janela": "dia"}, 846, 400]}))
R.append(dict(id="193-GRMMOR", resolucao="Res. ANA 193/2024", artigos="arts. 6º a 8º, 10 e 11", sistema="Rio Grande", ponto="M. Mascarenhas de Moraes (Peixoto)",
              descricao="Defluente ≤ teto da faixa e do período", tipo="teto", freq="mensal", niveis=3, tol=0.05,
              fonte="ONS:GRMMOR:defl", faixa="GRMMOR", janela="mes", umido=UMIDO_DEZ_ABR,
              tetos={"umido": [None, 500, 400], "seco": [None, {"param": "outorga_GRMMOR", "janela": "dia"}, 400]}))
for rid, nome in [("GRMARI", "Marimbondo"), ("GRAGVL", "Água Vermelha")]:
    R.append(dict(id=f"193-{rid}", resolucao="Res. ANA 193/2024", artigos="art. 9º", sistema="Rio Grande", ponto=nome,
                  descricao="VU ≥ 15% quando Furnas em Normal ou Atenção", tipo="vu_min", freq="diaria", niveis=2, tol=None,
                  fonte=f"ONS:{rid}:vu", minimo=15.0, condicao={"faixa": "GRFURN", "em": ["Normal", "Atenção"]}))

# --------------------------------------------------------------------------- 194/2024 Paranaíba
R.append(dict(id="194-PREMBO", resolucao="Res. ANA 194/2024", artigos="arts. 2º a 5º, 10 e 11", sistema="Paranaíba", ponto="T. C. Santiago (Emborcação)",
              descricao="Defluente ≤ teto da faixa e do período", tipo="teto", freq="mensal", niveis=3, tol=0.05,
              fonte="ONS:PREMBO:defl", faixa="PREMBO", janela="mes", umido=UMIDO_DEZ_ABR,
              tetos={"umido": [None, 140, 140], "seco": [{"param": "outorga_PREMBO", "janela": "dia"}, 786, 524]}))
R.append(dict(id="194-PRITUM", resolucao="Res. ANA 194/2024", artigos="arts. 6º a 8º, 10 e 11", sistema="Paranaíba", ponto="Itumbiara",
              descricao="Defluente ≤ teto da faixa e do período", tipo="teto", freq="mensal", niveis=3, tol=0.05,
              fonte="ONS:PRITUM:defl", faixa="PRITUM", janela="mes", umido=UMIDO_DEZ_ABR,
              tetos={"umido": [None, 784, 490], "seco": [{"param": "outorga_PRITUM", "janela": "dia"}, 1960, 1470]}))
R.append(dict(id="194-PRSSIM", resolucao="Res. ANA 194/2024", artigos="art. 9º", sistema="Paranaíba", ponto="São Simão",
              descricao="VU ≥ 15% quando Itumbiara em Normal ou Atenção", tipo="vu_min", freq="diaria", niveis=2, tol=None,
              fonte="ONS:PRSSIM:vu", minimo=15.0, condicao={"faixa": "PRITUM", "em": ["Normal", "Atenção"]}))

# --------------------------------------------------------------------------- 2.081/2017 São Francisco
ALERTA_2081 = "Nas faixas Atenção e Restrição, verificar o limite mensal fixado pela ANA. Sem esse valor, o painel aplica o limite legal, quando houver, ou indica \"Parâmetro pendente\"."
R.append(dict(id="2081-SFTMAR-min", resolucao="Res. ANA 2.081/2017", artigos="arts. 3º, I e § 1º; 5º; 12", sistema="São Francisco", ponto="Três Marias",
              descricao="Defluente média diária ≥ mínima da faixa", tipo="piso", freq="diaria", niveis=3, tol=0.05,
              fonte="ONS:SFTMAR:defl", faixa="SFTMAR", pisos={"todos": [150, 150, 100]}))
R.append(dict(id="2081-SFTMAR-max", alerta=ALERTA_2081, resolucao="Res. ANA 2.081/2017", artigos="art. 5º, §§ 2º e 3º; 12", sistema="São Francisco", ponto="Três Marias",
              descricao="Defluente média mensal ≤ máxima fixada para o mês", tipo="teto", freq="mensal", niveis=3, tol=0.05,
              fonte="ONS:SFTMAR:defl", faixa="SFTMAR", janela="mes",
              tetos={"todos": [None, {"param_mensal": "teto_SFTMAR"}, {"param_mensal": "teto_SFTMAR"}]}))
R.append(dict(id="2081-SFSOBR-min", resolucao="Res. ANA 2.081/2017", artigos="arts. 3º, II e § 2º; 9º; 12", sistema="São Francisco", ponto="Sobradinho",
              descricao="Defluente média diária ≥ mínima da faixa", tipo="piso", freq="diaria", niveis=3, tol=0.05,
              fonte="ONS:SFSOBR:defl", faixa="SFSOBR", pisos={"todos": [800, 800, 700]}))
R.append(dict(id="2081-SFSOBR-max", alerta=ALERTA_2081, resolucao="Res. ANA 2.081/2017", artigos="art. 9º, §§ 2º a 4º; 12", sistema="São Francisco", ponto="Sobradinho",
              descricao="Defluente média mensal ≤ máxima fixada (seco: ≤ 1.000 m³/s em Atenção)", tipo="teto", freq="mensal", niveis=3, tol=0.05,
              fonte="ONS:SFSOBR:defl", faixa="SFSOBR", janela="mes", umido=UMIDO_DEZ_ABR,
              tetos={"umido": [None, {"param_mensal": "teto_SFSOBR"}, {"param_mensal": "teto_SFSOBR"}],
                     "seco": [None, {"param_mensal": "teto_SFSOBR", "limite": 1000}, {"param_mensal": "teto_SFSOBR"}]}))
R.append(dict(id="2081-SFXING-min", resolucao="Res. ANA 2.081/2017", artigos="arts. 3º, III e § 3º; 9º; 12", sistema="São Francisco", ponto="Xingó",
              descricao="Defluente média diária ≥ mínima da faixa de Sobradinho", tipo="piso", freq="diaria", niveis=3, tol=0.05,
              fonte="ONS:SFXING:defl", faixa="SFSOBR", pisos={"todos": [1100, 800, 700]}))
R.append(dict(id="2081-SFXING-max", alerta=ALERTA_2081, resolucao="Res. ANA 2.081/2017", artigos="art. 9º, §§ 2º a 4º; 12", sistema="São Francisco", ponto="Xingó",
              descricao="Defluente média mensal ≤ máxima (Restrição: ≤ 900 m³/s)", tipo="teto", freq="mensal", niveis=3, tol=0.05,
              fonte="ONS:SFXING:defl", faixa="SFSOBR", janela="mes", umido=UMIDO_DEZ_ABR,
              tetos={"umido": [None, {"param_mensal": "teto_SFSOBR"}, 900],
                     "seco": [None, {"param_mensal": "teto_SFSOBR", "limite": 1000}, 900]}))
R.append(dict(id="2081-SFLGON", resolucao="Res. ANA 2.081/2017", artigos="art. 10, § 1º", sistema="São Francisco", ponto="Itaparica (Luiz Gonzaga)",
              descricao="VU ≥ 30% quando Sobradinho em Normal ou Atenção", tipo="vu_min", freq="diaria", niveis=2, tol=None,
              fonte="ONS:SFLGON:vu", minimo=30.0, condicao={"faixa": "SFSOBR", "em": ["Normal", "Atenção"]}))

# --------------------------------------------------------------------------- 70/2021 Tocantins
R.append(dict(id="70-TOSMES-min", resolucao="Res. ANA 70/2021", artigos="arts. 2º, 3º, I; 8º", sistema="Tocantins", ponto="Serra da Mesa",
              descricao="Defluente média diária ≥ 100 (úmido) / 300 m³/s (seco)", tipo="piso", freq="diaria", niveis=3, tol=0.05,
              fonte="ONS:TOSMES:defl", umido=UMIDO_DEZ_MAI, pisos={"umido": 100, "seco": 300}))
R.append(dict(id="70-TOSMES-max", resolucao="Res. ANA 70/2021", artigos="arts. 4º, 5º e 8º", sistema="Tocantins", ponto="Serra da Mesa",
              descricao="Defluente média mensal ≤ teto da faixa e do período", tipo="teto", freq="mensal", niveis=3, tol=0.05,
              fonte="ONS:TOSMES:defl", faixa="TOSMES", janela="mes", umido=UMIDO_DEZ_MAI,
              tetos={"umido": [None, 397], "seco": [None, 794, 397]}))
R.append(dict(id="70-TOUHET", resolucao="Res. ANA 70/2021", artigos="arts. 3º, II; 8º", sistema="Tocantins", ponto="Estreito",
              descricao="Defluente média diária ≥ 744 m³/s", tipo="piso", freq="diaria", niveis=3, tol=0.05,
              fonte="ONS:TOUHET:defl", pisos={"todos": 744}))
R.append(dict(id="70-TOSMES-praias", resolucao="Res. ANA 70/2021", artigos="arts. 6º e 8º", sistema="Tocantins", ponto="Serra da Mesa",
              descricao="Defluente constante na temporada de praias (10/6 a 20/8): ±5% da média do período", tipo="constante", freq="diaria", niveis=2, tol=0.05,
              fonte="ONS:TOSMES:defl", inicio="06-10", fim="08-20"))
for rid, nome, mn, mx in [("TOSMES", "Serra da Mesa", 417.30, 460.00), ("TOUHCB", "Cana Brava", 330.00, 333.00),
                          ("TOUHSA", "São Salvador", 286.50, 287.00), ("TOUHPA", "Peixe Angical", 261.00, 263.00),
                          ("TOLAJ", "Lajeado", 211.50, 212.30), ("TOUHET", "Estreito", 151.00, 156.00), ("TOTUCU", "Tucuruí", 51.60, 74.00)]:
    R.append(dict(id=f"70-nivel-{rid}", resolucao="Res. ANA 70/2021", artigos="art. 1º", sistema="Tocantins", ponto=nome,
                  descricao=f"Nível entre {mn:.2f} e {mx:.2f} m", tipo="intervalo", freq="diaria", niveis=2, tol=None,
                  fonte=f"ONS:{rid}:nivel", minimo=mn, maximo=mx))

# --------------------------------------------------------------------------- 49/2018 Foz do Chapecó
R.append(dict(id="49-RIUHFC", resolucao="Res. ANA 49/2018", artigos="art. 1º, caput e § 1º", sistema="Uruguai", ponto="Foz do Chapecó",
              descricao="Se afluência sex–dom ≤ 1.000 m³/s, defluente ≤ 1.000 m³/s de 12h de sexta a 12h de domingo (gatilho: afluência observada, proxy da previsão do ONS)",
              tipo="fds", freq="semanal", niveis=2, tol=None, fonte="ONSH:RIUHFC", limite=1000.0))

# --------------------------------------------------------------------------- 147/2015 Queimado
R.append(dict(id="147-SFQMDO-min", resolucao="Res. ANA 147/2015", artigos="art. 1º, I e II", sistema="Rio Preto", ponto="Queimado",
              descricao="Descarga mínima 8,8 (nov–abr) / 17 m³/s (mai–out)", tipo="piso", freq="diaria", niveis=2, tol=None,
              fonte="ONS:SFQMDO:defl", umido=UMIDO_NOV_ABR, pisos={"umido": 8.8, "seco": 17.0}))
R.append(dict(id="147-SFQMDO-vu", resolucao="Res. ANA 147/2015", artigos="art. 1º, III", sistema="Rio Preto", ponto="Queimado",
              descricao="VU ≥ 10% em 1º de maio e ao final de outubro", tipo="vu_datas", freq="anual", niveis=2, tol=None,
              fonte="ONS:SFQMDO:vu", minimo=10.0, datas=["05-01", "10-31"]))

# --------------------------------------------------------------------------- 1.382/2015 Paraíba do Sul (dado horário)
for rid, nome, alinea, piso in [("PBPARA", "Paraibuna", "a", 10), ("PBSTAB", "Santa Branca", "b", 30), ("PBJAGU", "Jaguari", "c", 4),
                                ("PBFUNI", "Funil", "d", 70), ("PBSTAC", "Santa Cecília (jusante)", "e", 71), ("RLPPAS", "Pereira Passos", "g", 120)]:
    R.append(dict(id=f"1382-{rid}", resolucao="Res. Conj. ANA/DAEE/IGAM/INEA 1.382/2015", artigos=f"art. 1º, I, {alinea}", sistema="Paraíba do Sul", ponto=nome,
                  descricao=f"Vazão a jusante ≥ {piso} m³/s (instantânea; menor valor horário do dia)", tipo="piso_horario", freq="diaria", niveis=2, tol=None,
                  fonte=f"ONSH:{rid}:defl", minimo=float(piso)))
R.append(dict(id="1382-PBSTAC-guandu", resolucao="Res. Conj. ANA/DAEE/IGAM/INEA 1.382/2015", artigos="art. 1º, I, f", sistema="Paraíba do Sul", ponto="Santa Cecília (bombeamento Guandu)",
              descricao="Vazão bombeada para o Guandu ≥ 119 m³/s (média diária); tolerância de 5% (113,05 m³/s), critério da SOE", tipo="piso", freq="diaria", niveis=3, tol=0.05,
              fonte="ONS:PBSTAC:transf", pisos={"todos": 119},
              alerta="A resolução não prevê exceção. Nos dias em \"Não atende\", verificar se a redução foi justificada (cheia, manutenção ou falha de equipamento)."))
R.append(dict(id="1382-estagios", resolucao="Res. Conj. ANA/DAEE/IGAM/INEA 1.382/2015", artigos="art. 1º, V e § 1º", sistema="Paraíba do Sul", ponto="Funil, Santa Branca, Paraibuna, Jaguari",
              descricao="VU ≥ mínimo do estágio de deplecionamento vigente (variação absoluta de 5 p.p. do valor de referência)", tipo="estagios", freq="diaria", niveis=3, tol=None, tol_pp=5.0,
              fontes={"PBFUNI": [30, 30, 30], "PBSTAB": [70, 40, 10], "PBPARA": [80, 40, 5], "PBJAGU": [80, 50, 20]}))

# --------------------------------------------------------------------------- 1.931/2017 Jaguari → Atibainha
R.append(dict(id="1931-max", resolucao="Res. ANA 1.931/2017", artigos="art. 1º, II", sistema="Transposição Jaguari–Atibainha", ponto="Captação no Jaguari",
              descricao="Vazão de captação ≤ 8,5 m³/s (média diária da EEAB Jaguari, Sabesp, 07h–07h)", tipo="teto_diario", freq="diaria", niveis=2, tol=None,
              fonte="SABESP:jaguari_ps:q", tetos={"todos": 8.5}))
R.append(dict(id="1931-inverso", resolucao="Res. ANA 1.931/2017", artigos="art. 1º, V", sistema="Transposição Jaguari–Atibainha", ponto="Captação no Atibainha (sentido inverso)",
              descricao="Vazão de captação no Atibainha ≤ 12,2 m³/s (média diária da EEAB Jaguari, Sabesp, valores negativos)", tipo="teto_diario", freq="diaria", niveis=2, tol=None,
              fonte="SABESP:jaguari_ps:q", tetos={"todos": 12.2}, sentido="inverso"))
R.append(dict(id="1931-volume", resolucao="Res. ANA 1.931/2017", artigos="art. 1º, III (Comunicado Conjunto nº 3/2026)", sistema="Transposição Jaguari–Atibainha", ponto="Captação no Jaguari",
              descricao="Volume anual captado ≤ limite vigente", tipo="volume_anual", freq="anual", niveis=2, tol=None,
              fonte="SABESP:jaguari_ps:q", param="vol_anual_jaguari"))
R.append(dict(id="1931-cota", resolucao="Res. ANA 1.931/2017", artigos="art. 1º, IV", sistema="Transposição Jaguari–Atibainha", ponto="Reservatório Jaguari",
              descricao="Captação só com Jaguari entre 603,20 e 623,00 m", tipo="intervalo_se", freq="diaria", niveis=2, tol=None,
              fonte="ONS:PBJAGU:nivel", condicao_fonte="ONS:PBJAGU:transf", minimo=603.20, maximo=623.00))

# --------------------------------------------------------------------------- 925/2017 Cantareira
R.append(dict(id="925-santaines", resolucao="Res. Conj. ANA/DAEE 925/2017", artigos="arts. 4º, §§ 1º e 2º; 6º", sistema="Cantareira", ponto="Elevatória Santa Inês",
              descricao="Captação média mensal ≤ limite da faixa (+ bombeado do Jaguari nas faixas 2 a 4)", tipo="teto_cantareira", freq="mensal", niveis=2, tol=None,
              fonte="SABESP:santa_ines:q", faixa="CANTAREIRA", limites=[33.0, 31.0, 27.0, 23.0, 15.5], soma_transf="ONS:PBJAGU:transf"))
for chave, nome, movel, minimo in [("valinhos", "Valinhos (rio Atibaia)", [12.0, 12.0, 11.0, 11.0, None], 10.0),
                                   ("atibaia", "Atibaia (rio Atibaia)", [3.0, 3.0, 2.0, 2.0, None], 2.0),
                                   ("buenopolis", "Buenópolis (rio Jaguari)", [2.5, 2.5, 2.0, 2.0, None], 2.0)]:
    R.append(dict(id=f"925-{chave}-dia", resolucao="Res. Conj. ANA/DAEE 925/2017", artigos="art. 5º, §§ 3º, 6º e 10", sistema="Cantareira (PCJ)", ponto=nome,
                  descricao=f"Vazão média diária ≥ {minimo:.1f} m³/s".replace(".", ","), tipo="piso", freq="diaria", niveis=2, tol=None,
                  fonte=f"SAISP:{chave}", pisos={"todos": minimo}, sem_faixa5=(chave == "atibaia"), faixa="CANTAREIRA"))
    R.append(dict(id=f"925-{chave}-movel", resolucao="Res. Conj. ANA/DAEE 925/2017", artigos="art. 5º, § 2º", sistema="Cantareira (PCJ)", ponto=nome,
                  descricao="Média móvel de 15 dias ≥ meta da faixa (só no período úmido)", tipo="movel", freq="diaria", niveis=2, tol=None,
                  fonte=f"SAISP:{chave}", faixa="CANTAREIRA", metas=movel, umido=UMIDO_DEZ_MAI, dias=15))
R.append(dict(id="925-seco", resolucao="Res. Conj. ANA/DAEE 925/2017", artigos="art. 5º, § 5º", sistema="Cantareira (PCJ)", ponto="Sistema Cantareira → PCJ",
              descricao="Informativa: volume garantido ao PCJ de 1º/6 a 30/11 (média de 10 m³/s, 158,1 hm³) e volume liberado", tipo="media_periodo", freq="periodo", niveis=2, tol=None, informativa=True,
              fontes=["SAR:29001:defl", "SAR:29002:defl", "SAR:29003:defl"], inicio="06-01", fim="11-30", minimo=10.0))
for cod, nome, mn in [("29004", "Paiva Castro", 0.10), ("29001", "Jaguari-Jacareí", 0.25)]:
    R.append(dict(id=f"925-art2-{cod}", resolucao="Res. Conj. ANA/DAEE 925/2017", artigos="art. 2º", sistema="Cantareira", ponto=nome,
                  descricao=f"Descarga para jusante ≥ {mn:.2f} m³/s".replace(".", ","), tipo="piso", freq="diaria", niveis=2, tol=None,
                  fonte=f"SAR:{cod}:defl", pisos={"todos": mn}))
R.append(dict(id="925-art2-atibaia", resolucao="Res. Conj. ANA/DAEE 925/2017", artigos="art. 2º, III", sistema="Cantareira", ponto="Cachoeira + Atibainha",
              descricao="Descarga conjunta para o rio Atibaia ≥ 0,25 m³/s", tipo="piso", freq="diaria", niveis=2, tol=None,
              fonte="SOMA:SAR:29002:defl+SAR:29003:defl", pisos={"todos": 0.25}))

# --------------------------------------------------------------------------- 614/2010 Cantareira (cheias, out–jun)
for cod, nome, maxnormal in [("29001", "Jaguari-Jacareí", 844.00), ("29002", "Cachoeira", 821.88), ("29003", "Atibainha", 786.72), ("29004", "Paiva Castro", 745.61)]:
    R.append(dict(id=f"614-{cod}", resolucao="Res. Conj. ANA/DAEE 614/2010", artigos="arts. 1º, 3º a 5º; Anexo 1", sistema="Cantareira (cheias)", ponto=nome,
                  descricao=f"Out–jun: cota ≤ NA máximo normal ({maxnormal:.2f} m)".replace(".", ","), tipo="teto_cota", freq="diaria", niveis=2, tol=None,
                  fonte=f"SAR:{cod}:cota", maximo=maxnormal, meses=[10, 11, 12, 1, 2, 3, 4, 5, 6]))

for cod, nome, seco, umido in [("29001", "Jaguari-Jacareí", 40, 100), ("29002", "Cachoeira", 5, 7), ("29003", "Atibainha", 5, 11), ("29004", "Paiva Castro", 1, 1)]:
    R.append(dict(id=f"614-restr-{cod}", resolucao="Res. Conj. ANA/DAEE 614/2010", artigos="Anexo 1, item 4", sistema="Cantareira (cheias)", ponto=nome,
                  descricao=f"Descarga ≤ vazão de restrição ({seco} m³/s jul–nov; {umido} m³/s dez–jun)", tipo="teto_diario", freq="diaria", niveis=2, tol=None,
                  fonte=f"SAR:{cod}:defl", umido=[12, 1, 2, 3, 4, 5, 6], tetos={"umido": umido, "seco": seco}))

REGRAS = R
