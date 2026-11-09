# Painel de monitoramento das regras de operação de reservatórios (SOE/ANA)

Painel estático que audita diariamente o cumprimento das resoluções e marcos regulatórios de operação dos sistemas hídricos acompanhados pela Superintendência de Operações e Eventos Críticos (SOE) da ANA. O cálculo roda no GitHub Actions e o resultado é publicado no GitHub Pages.

## O que é auditado

62 regras, em 12 sistemas hídricos, com base nas resoluções validadas pela SOE em 03/11/2026:

| Sistema | Normas |
|---|---|
| Paranapanema | Res. ANA 132/2022 |
| Rio Grande e Paranaíba | Res. ANA 193/2024 e 194/2024 |
| São Francisco | Res. ANA 2.081/2017; Res. ANA 147/2015 (Queimado, rio Preto) |
| Tocantins | Res. ANA 70/2021 |
| Uruguai | Res. ANA 49/2018 (Foz do Chapecó) |
| Paraíba do Sul | Res. Conj. ANA/DAEE/IGAM/INEA 1.382/2015 |
| Cantareira, PCJ e cheias | Res. Conj. ANA/DAEE 925/2017; Res. Conj. ANA/DAEE 614/2010 (cheias) |
| Transposição Jaguari–Atibainha | Res. ANA 1.931/2017 e Comunicado Conjunto 3/2026 |

A lista completa, com dispositivos, limites e fontes, está em `src/regras.py`.

## Fontes de dados

| Dado | Fonte | Uso |
|---|---|---|
| Níveis, volumes e vazões diários | ONS Dados Abertos, `DADOS_HIDROLOGICOS_RES` | regras diárias, semanais, mensais e anuais |
| Vazões horárias | ONS Dados Abertos, `DADOS_HIDROLOGICOS_HO` | regras intradiárias do Paraíba do Sul |
| Reservatórios do Cantareira | SAR/ANA (`MedicaoCantareira`); reserva: API Mananciais da Sabesp | cotas de cheia (Res. 614/2010), descargas (Res. 925/2017) e volume equivalente |
| Postos do PCJ (Valinhos, Atibaia, Buenópolis) | SAISP/DAEE | vazões mínimas e médias móveis (Res. 925) |
| Faixas operativas vigentes | Boletins diários da Sala de Situação/ANA (gov.br); painéis Power BI da SOE quando não há boletim | faixa aplicável no mês ou na semana operativa |
| Captação da EESI (Santa Inês) | API Mananciais da Sabesp, elemento 36 (`/api/v4/elementos/36/dados/3/diario/...`) | teto mensal da Res. 925/2017; dia de 07h a 07h |
| Captação da EEAB Jaguari (transposição) | API Mananciais da Sabesp, elemento 48, parâmetro 3 | vazão máxima e volume anual acumulado desde 1º/jan da Res. 1.931/2017 (média diária, 07h a 07h) |

## Estrutura

```
src/coleta.py              coletores (ONS, SAR, SAISP, API Mananciais Sabesp, Telemetria ANA, arquivos manuais)
src/regras.py              faixas, parâmetros e as 62 regras
src/calculo.py             avaliação diária, status e folga -> data/painel.json
src/boletins_ana.py        coleta diária das faixas nos boletins oficiais da ANA (gov.br)
src/boletins_parsers.py    leitura do texto dos boletins (pdftotext)
src/powerbi_faixas_soe.py  extração das faixas dos painéis Power BI
src/faixas_powerbi.py      converte as faixas extraídas em config/faixas.json (histórico)
src/faixas_boletins.py     grava em config/faixas.json o histórico de faixas dos boletins diários (desde out/2017)
src/publicar.py            monta o site (_site/index.html + painel.json)
src/validar.py             confronta o cálculo com a planilha de auditoria da SOE
web/painel.html            interface do painel
config/faixas.json         faixas publicadas (boletim ANA ou Power BI), por reservatório e período
config/parametros_mensais.json  valores mensais fixados por ato (2.081/2017 e 614/2010)
config/curvas_seguranca_sf.json curvas de segurança de Três Marias e Sobradinho por ciclo (2.081/2017)
data/manual/historico/     série manual de Santa Inês usada até out/2026 (substituída pela API Sabesp)
.github/workflows/painel.yml    atualização diária e publicação (cópia em workflow-painel.yml)
```

## Executar localmente

Requer Python 3.10 ou superior.

Período: o painel cobre desde 01/10/2017 (início do histórico de faixas nos boletins do Cantareira). Antes da entrada em vigor de cada resolução (`VIGENCIA` em `src/regras.py`, conforme o texto oficial), a regra aparece como "Não se aplica". As Res. 1.382/2015 e 2.081/2017 entram em vigor após comunicado da ANA: 01/12/2016 (Ofício Circular nº 48/2016/AA-ANA) e 01/05/2019 (Ofício Circular nº 1/2019/AA-CD-ANA), respectivamente. Coleta, cálculo e publicação usam apenas a biblioteca padrão.

```bash
python src/powerbi_faixas_soe.py data/powerbi
python src/faixas_powerbi.py data/powerbi/faixas_operativas_vigentes.csv
python src/calculo.py                    # desde 01/10/2017 (1ª execução sem cache: ~1 h de download)
python src/publicar.py
python -m http.server -d _site 8000      # abrir http://localhost:8000
```

Validação contra a planilha de auditoria (requer `pip install openpyxl`):

```bash
python src/validar.py "Planilha de Auditoria Multitemporal.xlsx" data/painel.json data/validacao.csv
```

## Status e métrica

- Três níveis (Atende, Tolerância, Não atende) somente onde a resolução prevê tolerância (132/2022, 193/2024, 194/2024, 2.081/2017, 70/2021 e estágios da 1.382/2015, com variação absoluta de 5 p.p. de VU) e no bombeamento ao Guandu (5% de 119 m³/s desde 20/08/2021, Ofício Circular nº 1/2021/JG/ANA); dois níveis nos demais casos.
- "Não se aplica": regra fora da sua condição de aplicação (período, faixa ou gatilho).
- "Sem dado": a fonte não publicou o dado do dia.
- "Parâmetro pendente": o limite depende de ato que não está cadastrado; a observação do dia diz qual ato ou informação falta.

## Manutenção

- **Faixas**: a cada execução, o workflow lê as faixas nesta ordem: (1) boletim diário oficial da ANA no gov.br (`src/boletins_ana.py`); (2) painéis Power BI da SOE, só para período sem boletim; (3) faixa calculada pela regra da resolução, quando não há publicação. O histórico fica em `config/faixas.json`, e o painel indica a origem de cada faixa. No Paranapanema e no Grande e Paranaíba, o boletim só é aceito a partir de segunda-feira e do dia 4 do mês, respectivamente, porque os boletins do início do período ainda podem trazer a faixa anterior.
- **Texto das normas**: os textos das resoluções ficam em `normas/textos/r<número>_<ano>.txt`. `src/textos_normas.py` recorta os dispositivos citados no campo `artigos` de cada regra, e o painel os mostra na coluna Norma e no detalhe. Ao cadastrar uma resolução nova, incluir o texto e conferir o recorte com `python src/textos_normas.py`. Citação ambígua vai em `CORRECOES`.
- **Regras que dependem de ato complementar**: o dicionário `COMPLEMENTAR` em `src/regras.py` indica, por regra, o ato posterior à resolução de que o parâmetro depende. O painel mostra o selo "ato complementar" e o filtro "Só regras com ato complementar" na aba Regras. Ao cadastrar nova regra nessa condição, incluir a entrada no dicionário.
- **Curvas de segurança do São Francisco** (Res. 2.081/2017, art. 4º): a cada ciclo (dezembro a novembro), acrescentar as tabelas de Três Marias e de Xingó da NT do ONS em `config/curvas_seguranca_sf.json`, com o ofício de não objeção da ANA em `manifestacao_ana`. Sem esse ofício, o teto fica como Parâmetro pendente.
- **Valores mensais por ato** (`config/parametros_mensais.json`, chave `"<parâmetro>|aaaa-mm"`): `teto_SFTMAR`, `teto_SFXING` e `teto_SFSOBR` (vazões da faixa de Restrição fixadas pelo ONS a partir de recomendação da ANA; um valor informado prevalece sobre a curva) e `na_op_<código SAR>` (nível máximo operacional mensal do DAEE, Res. 614/2010).
- **Revisões temporárias dos estágios da 1.382/2015** (art. 1º, § 2º): lista `revisoes` da regra `1382-estagios` em `src/regras.py`.
- **Santa Inês**: coletada automaticamente da API Mananciais da Sabesp (`https://mananciais.sabesp.com.br/api/v4`, EEAB 36). O valor do dia D é a média de D-1 07h a D 07h. A planilha manual de out/2026 ficou em `data/manual/historico/` só como registro.
- **Postos PCJ**: continuam no SAISP, com média diária de 00h a 24h (decisão da SOE em 04/11/2026). A API Sabesp também publica esses postos, mas no dia de 07h a 07h, e por isso não é usada para eles.
- **Rede**: o ambiente que roda o cálculo precisa acessar `mananciais.sabesp.com.br`.

## Publicar no GitHub (primeira vez)

1. **Criar a conta.** Acesse https://github.com/signup, informe o e-mail, crie a senha e o nome de usuário e confirme o código enviado por e-mail. O plano gratuito atende.
2. **Criar o repositório.** No canto superior direito, clique em **+** e depois em **New repository**. Nome sugerido: `painel-regras-operacao`. Marque **Public** (o GitHub Pages gratuito exige repositório público) e clique em **Create repository**.
3. **Enviar os arquivos.** Na página do repositório vazio, clique em **uploading an existing file** e arraste as pastas `config`, `data`, `src` e `web` e os arquivos `README.md`, `.gitignore` e `requirements.txt`. Clique em **Commit changes**.
   - **Workflow:** clique em **Add file > Create new file**, digite o nome `.github/workflows/painel.yml` (as barras criam as pastas), cole o conteúdo de `workflow-painel.yml` e clique em **Commit changes**. O arquivo de workflow precisa ficar exatamente nesse caminho para o GitHub reconhecê-lo.
4. **Ativar o GitHub Pages.** Em **Settings > Pages**, em **Build and deployment > Source**, escolha **GitHub Actions**.
5. **Permitir que o workflow grave o histórico de faixas.** Em **Settings > Actions > General > Workflow permissions**, marque **Read and write permissions** e salve.
6. **Executar pela primeira vez.** Em **Actions**, selecione **Atualizar painel** e clique em **Run workflow**. Ao final (a 1ª execução leva cerca de 1 hora, pois baixa o histórico desde out/2017; as seguintes, poucos minutos), o endereço do painel aparece no resumo da execução, no formato `https://<usuario>.github.io/painel-regras-operacao/`.

Depois disso, o painel é atualizado todos os dias às 11h e às 17h (horário de Brasília), por disparo externo do cron-job.org (`workflow_dispatch`).

### Limitações conhecidas

- Os servidores do GitHub ficam fora do Brasil. O SAR/ANA e o SAISP às vezes recusam ou demoram a responder a acessos externos. A falha de uma fonte não interrompe o painel: as regras afetadas aparecem como "Sem dado" e a falha é registrada nas notas do rodapé.
- O painel é público. Não inclua dados restritos em `data/manual/`.
