import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

DB_NAME = "gastos_alta_direcao.db"

st.set_page_config(
    page_title="Despesas ao Controle Total",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ==============================
# BANCO DE DADOS
# ==============================
def get_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


conn = get_connection()


def init_db():
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS usuario (
            id_usuario INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            perfil TEXT,
            renda_mensal REAL DEFAULT 0,
            limite_orcamento REAL DEFAULT 0,
            objetivo_financeiro TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS categorias (
            id_categoria INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_categoria TEXT NOT NULL UNIQUE,
            tipo_categoria TEXT NOT NULL,
            subcategoria_padrao TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS despesas (
            id_despesa INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            descricao TEXT NOT NULL,
            categoria TEXT NOT NULL,
            subcategoria TEXT,
            valor REAL NOT NULL,
            meio_pagamento TEXT NOT NULL,
            tipo_gasto TEXT NOT NULL,
            prioridade TEXT NOT NULL,
            recorrente INTEGER DEFAULT 0,
            parcela_atual INTEGER DEFAULT 1,
            total_parcelas INTEGER DEFAULT 1,
            vencimento TEXT,
            status_pagamento TEXT NOT NULL,
            observacao TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS orcamentos (
            id_orcamento INTEGER PRIMARY KEY AUTOINCREMENT,
            mes TEXT NOT NULL,
            categoria TEXT NOT NULL,
            valor_planejado REAL NOT NULL,
            valor_realizado REAL DEFAULT 0,
            diferenca REAL DEFAULT 0,
            UNIQUE(mes, categoria)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alertas (
            id_alerta INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo_alerta TEXT NOT NULL,
            descricao TEXT NOT NULL,
            data_vencimento TEXT,
            status TEXT NOT NULL
        )
        """
    )

    conn.commit()
    seed_data()
    ensure_default_user()


def seed_data():
    cursor = conn.cursor()
    categorias_padrao = [
        ("Alimentação", "Variável", "Restaurantes e mercado"),
        ("Transporte", "Variável", "Combustível e apps"),
        ("Moradia", "Fixo", "Aluguel e condomínio"),
        ("Saúde", "Fixo", "Plano e consultas"),
        ("Lazer", "Variável", "Eventos e passeios"),
        ("Educação", "Fixo", "Cursos e livros"),
        ("Viagens", "Variável", "Passagens e hospedagem"),
        ("Compras Pessoais", "Variável", "Roupas e acessórios"),
        ("Assinaturas", "Fixo", "Streaming e softwares"),
        ("Contas Fixas", "Fixo", "Internet, água, energia"),
        ("Imprevistos", "Variável", "Emergências"),
    ]

    for categoria in categorias_padrao:
        cursor.execute(
            """
            INSERT OR IGNORE INTO categorias (nome_categoria, tipo_categoria, subcategoria_padrao)
            VALUES (?, ?, ?)
            """,
            categoria,
        )

    conn.commit()


def ensure_default_user():
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS total FROM usuario")
    total = cursor.fetchone()["total"]
    if total == 0:
        cursor.execute(
            """
            INSERT INTO usuario (nome, perfil, renda_mensal, limite_orcamento, objetivo_financeiro)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Diretor Executivo",
                "Alta Direção",
                35000.00,
                25000.00,
                "Controlar gastos e ampliar capacidade de investimento",
            ),
        )
        conn.commit()


init_db()


# ==============================
# FUNÇÕES AUXILIARES
# ==============================
def format_currency(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def run_query(query, params=None):
    params = params or []
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    return cur


def read_df(query, params=None):
    params = params or []
    return pd.read_sql_query(query, conn, params=params)


def get_categories():
    df = read_df("SELECT * FROM categorias ORDER BY nome_categoria")
    if df.empty:
        return []
    return df["nome_categoria"].tolist()


def get_user():
    df = read_df("SELECT * FROM usuario ORDER BY id_usuario LIMIT 1")
    return df.iloc[0] if not df.empty else None


def update_budget_realizado(mes_referencia):
    despesas_mes = read_df(
        """
        SELECT categoria, SUM(valor) AS valor_realizado
        FROM despesas
        WHERE substr(data, 1, 7) = ?
        GROUP BY categoria
        """,
        [mes_referencia],
    )

    orc_df = read_df("SELECT * FROM orcamentos WHERE mes = ?", [mes_referencia])
    if orc_df.empty:
        return

    for _, row in orc_df.iterrows():
        valor_realizado = 0.0
        filtrado = despesas_mes[despesas_mes["categoria"] == row["categoria"]]
        if not filtrado.empty:
            valor_realizado = float(filtrado.iloc[0]["valor_realizado"])
        diferenca = float(row["valor_planejado"]) - valor_realizado
        run_query(
            """
            UPDATE orcamentos
            SET valor_realizado = ?, diferenca = ?
            WHERE id_orcamento = ?
            """,
            [valor_realizado, diferenca, int(row["id_orcamento"])],
        )


def refresh_alerts():
    hoje = date.today()
    limite = hoje + timedelta(days=7)

    run_query("DELETE FROM alertas")

    proximos = read_df(
        """
        SELECT descricao, vencimento, valor, status_pagamento
        FROM despesas
        WHERE vencimento IS NOT NULL AND vencimento <> ''
        """
    )

    for _, row in proximos.iterrows():
        try:
            venc = datetime.strptime(row["vencimento"], "%Y-%m-%d").date()
            if row["status_pagamento"] != "Pago":
                if hoje <= venc <= limite:
                    run_query(
                        """
                        INSERT INTO alertas (tipo_alerta, descricao, data_vencimento, status)
                        VALUES (?, ?, ?, ?)
                        """,
                        [
                            "Vencimento Próximo",
                            f"A despesa '{row['descricao']}' vence em breve no valor de {format_currency(row['valor'])}.",
                            row["vencimento"],
                            "Ativo",
                        ],
                    )
                elif venc < hoje:
                    run_query(
                        """
                        INSERT INTO alertas (tipo_alerta, descricao, data_vencimento, status)
                        VALUES (?, ?, ?, ?)
                        """,
                        [
                            "Despesa Vencida",
                            f"A despesa '{row['descricao']}' está vencida no valor de {format_currency(row['valor'])}.",
                            row["vencimento"],
                            "Ativo",
                        ],
                    )
        except Exception:
            pass

    mes_atual = date.today().strftime("%Y-%m")
    update_budget_realizado(mes_atual)
    excessos = read_df(
        """
        SELECT categoria, valor_planejado, valor_realizado
        FROM orcamentos
        WHERE mes = ? AND valor_realizado > valor_planejado
        """,
        [mes_atual],
    )

    for _, row in excessos.iterrows():
        excedente = float(row["valor_realizado"]) - float(row["valor_planejado"])
        run_query(
            """
            INSERT INTO alertas (tipo_alerta, descricao, data_vencimento, status)
            VALUES (?, ?, ?, ?)
            """,
            [
                "Orçamento Excedido",
                f"A categoria '{row['categoria']}' excedeu o orçamento em {format_currency(excedente)}.",
                None,
                "Ativo",
            ],
        )


def export_excel(df_dict):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        for sheet_name, df in df_dict.items():
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    output.seek(0)
    return output


def get_theme_mode():
    if "theme_mode" not in st.session_state:
        st.session_state.theme_mode = "light"
    return st.session_state.theme_mode


def toggle_theme_mode():
    st.session_state.theme_mode = "dark" if get_theme_mode() == "light" else "light"


def get_theme_tokens():
    if get_theme_mode() == "dark":
        return {
            "name": "Escuro",
            "bg": "#0b1320",
            "bg_alt": "#111c2e",
            "surface": "rgba(12, 21, 37, 0.78)",
            "surface_strong": "#16253a",
            "surface_soft": "rgba(22, 37, 58, 0.88)",
            "text": "#ecf3fb",
            "muted": "#9cb0c7",
            "accent": "#24b8a6",
            "accent_alt": "#f08a4b",
            "border": "rgba(148, 163, 184, 0.18)",
            "shadow": "0 24px 64px rgba(2, 6, 23, 0.45)",
            "sidebar": "rgba(7, 14, 26, 0.94)",
            "pill": "rgba(36, 184, 166, 0.10)",
            "scheme": "dark",
            "button_text": "#081018",
        }
    return {
        "name": "Claro",
        "bg": "#f4efe7",
        "bg_alt": "#ebe0d0",
        "surface": "rgba(255, 250, 242, 0.78)",
        "surface_strong": "#fffaf3",
        "surface_soft": "rgba(247, 239, 229, 0.88)",
        "text": "#1d2733",
        "muted": "#5d6672",
        "accent": "#0f766e",
        "accent_alt": "#c56a2d",
        "border": "rgba(15, 23, 42, 0.10)",
        "shadow": "0 24px 64px rgba(15, 23, 42, 0.10)",
        "sidebar": "rgba(255, 248, 239, 0.95)",
        "pill": "rgba(15, 118, 110, 0.08)",
        "scheme": "light",
        "button_text": "#f8fafc",
    }


def apply_theme():
    theme = get_theme_tokens()
    px.defaults.template = "plotly_dark" if theme["scheme"] == "dark" else "plotly_white"

    st.markdown(
        f"""
        <style>
            :root {{
                color-scheme: {theme["scheme"]};
            }}

            .stApp {{
                color: {theme["text"]};
                background:
                    radial-gradient(circle at top left, {theme["pill"]}, transparent 28%),
                    radial-gradient(circle at top right, rgba(197, 106, 45, 0.12), transparent 24%),
                    linear-gradient(180deg, {theme["bg"]} 0%, {theme["bg_alt"]} 100%);
            }}

            [data-testid="stAppViewContainer"] > .main,
            [data-testid="stHeader"] {{
                background: transparent;
            }}

            .block-container {{
                padding-top: 1.25rem;
                padding-bottom: 2rem;
                max-width: 1240px;
            }}

            [data-testid="stSidebar"] > div:first-child {{
                background: {theme["sidebar"]};
                border-right: 1px solid {theme["border"]};
            }}

            h1, h2, h3, h4, h5, h6 {{
                color: {theme["text"]};
                font-family: Cambria, Georgia, serif;
                letter-spacing: -0.02em;
            }}

            p, li, label, .stCaption, .stMarkdown, .stText, .st-emotion-cache-10trblm {{
                color: {theme["text"]};
            }}

            small {{
                color: {theme["muted"]};
            }}

            div[data-baseweb="input"] > div,
            div[data-baseweb="select"] > div,
            div[data-baseweb="textarea"] > div,
            .stDateInput > div > div,
            .stNumberInput > div > div {{
                background: {theme["surface_strong"]} !important;
                border: 1px solid {theme["border"]} !important;
                border-radius: 14px !important;
                color: {theme["text"]} !important;
            }}

            div[data-baseweb="input"] input,
            div[data-baseweb="select"] input,
            div[data-baseweb="textarea"] textarea,
            .stDateInput input,
            .stNumberInput input {{
                color: {theme["text"]} !important;
            }}

            div[data-testid="stForm"] {{
                background: {theme["surface"]};
                border: 1px solid {theme["border"]};
                border-radius: 24px;
                padding: 1.25rem 1.1rem 0.6rem;
                box-shadow: {theme["shadow"]};
                backdrop-filter: blur(14px);
            }}

            [data-testid="stMetric"] {{
                background: {theme["surface"]};
                border: 1px solid {theme["border"]};
                border-radius: 22px;
                padding: 1rem 1.1rem;
                box-shadow: {theme["shadow"]};
            }}

            [data-testid="stMetricLabel"],
            [data-testid="stMetricValue"] {{
                color: {theme["text"]};
            }}

            .stButton > button,
            .stDownloadButton > button,
            div[data-testid="stFormSubmitButton"] button {{
                border: none;
                border-radius: 14px;
                padding: 0.7rem 1rem;
                font-weight: 700;
                box-shadow: 0 18px 34px rgba(15, 23, 42, 0.18);
                background: linear-gradient(135deg, {theme["accent"]}, {theme["accent_alt"]});
                color: {theme["button_text"]};
                transition: transform 0.18s ease, box-shadow 0.18s ease;
            }}

            .stButton > button:hover,
            .stDownloadButton > button:hover,
            div[data-testid="stFormSubmitButton"] button:hover {{
                transform: translateY(-1px);
                box-shadow: 0 22px 38px rgba(15, 23, 42, 0.24);
            }}

            div[data-testid="stDataFrame"],
            div[data-testid="stTable"] {{
                background: {theme["surface"]};
                border: 1px solid {theme["border"]};
                border-radius: 22px;
                overflow: hidden;
                box-shadow: {theme["shadow"]};
            }}

            button[data-baseweb="tab"] {{
                border-radius: 14px;
                background: {theme["surface"]};
                border: 1px solid {theme["border"]};
                color: {theme["text"]};
                margin-right: 0.35rem;
            }}

            .hero-shell,
            .content-card,
            .summary-card,
            .stage-card {{
                background: {theme["surface"]};
                border: 1px solid {theme["border"]};
                border-radius: 28px;
                box-shadow: {theme["shadow"]};
            }}

            .hero-shell {{
                padding: 1.6rem;
                margin-bottom: 1.25rem;
                overflow: hidden;
            }}

            .hero-grid {{
                display: grid;
                grid-template-columns: minmax(0, 1.8fr) minmax(280px, 1fr);
                gap: 1rem;
                align-items: stretch;
            }}

            .hero-kicker {{
                font-size: 0.76rem;
                letter-spacing: 0.18em;
                text-transform: uppercase;
                color: {theme["muted"]};
                font-weight: 700;
                margin-bottom: 0.8rem;
            }}

            .hero-title {{
                font-size: clamp(2.4rem, 4vw, 4.2rem);
                line-height: 0.95;
                margin: 0 0 0.85rem 0;
            }}

            .hero-copy {{
                font-size: 1.04rem;
                line-height: 1.7;
                color: {theme["muted"]};
                max-width: 52rem;
                margin-bottom: 0;
            }}

            .chip-row {{
                display: flex;
                flex-wrap: wrap;
                gap: 0.6rem;
                margin-top: 1rem;
            }}

            .chip {{
                background: {theme["pill"]};
                border: 1px solid {theme["border"]};
                border-radius: 999px;
                color: {theme["text"]};
                padding: 0.48rem 0.82rem;
                font-size: 0.86rem;
            }}

            .hero-panel {{
                background: {theme["surface_soft"]};
                border: 1px solid {theme["border"]};
                border-radius: 22px;
                padding: 1rem;
                display: grid;
                gap: 0.8rem;
                align-content: start;
            }}

            .panel-stat {{
                background: {theme["surface_strong"]};
                border: 1px solid {theme["border"]};
                border-radius: 18px;
                padding: 0.9rem 1rem;
            }}

            .panel-label,
            .summary-label,
            .stage-label,
            .theme-badge {{
                font-size: 0.75rem;
                letter-spacing: 0.12em;
                text-transform: uppercase;
                color: {theme["muted"]};
                font-weight: 700;
            }}

            .panel-value,
            .summary-value {{
                color: {theme["text"]};
                font-size: 1.35rem;
                font-weight: 700;
                margin-top: 0.35rem;
            }}

            .content-card,
            .summary-card,
            .stage-card {{
                padding: 1.25rem;
                height: 100%;
            }}

            .content-card p,
            .summary-card p,
            .stage-card p {{
                color: {theme["muted"]};
                line-height: 1.6;
                margin-bottom: 0;
            }}

            .content-card ul {{
                margin: 0.7rem 0 0 1rem;
                color: {theme["muted"]};
            }}

            .content-card li {{
                margin-bottom: 0.3rem;
            }}

            .section-title {{
                margin: 0 0 0.6rem 0;
                font-size: 1.2rem;
            }}

            .categories-strip {{
                display: flex;
                flex-wrap: wrap;
                gap: 0.55rem;
                margin-top: 0.9rem;
            }}

            .theme-inline {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 1rem;
                margin-bottom: 1rem;
            }}

            .theme-note {{
                color: {theme["muted"]};
                font-size: 0.92rem;
            }}

            hr {{
                border-color: {theme["border"]};
            }}

            @media (max-width: 900px) {{
                .hero-grid {{
                    grid-template-columns: 1fr;
                }}
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_summary_card(title, value, description):
    st.markdown(
        f"""
        <div class="summary-card">
            <div class="summary-label">{escape(title)}</div>
            <div class="summary-value">{escape(value)}</div>
            <p>{escape(description)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def login_block():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False

    if not st.session_state.logged_in:
        user = get_user()
        limite_padrao = format_currency(float(user["limite_orcamento"])) if user is not None else format_currency(0)
        renda_padrao = format_currency(float(user["renda_mensal"])) if user is not None else format_currency(0)

        topo_esq, topo_dir = st.columns([5, 1.25])
        with topo_esq:
            st.markdown(
                f"""
                <div class="theme-inline">
                    <div>
                        <div class="theme-badge">Ambiente Executivo</div>
                        <div class="theme-note">Tema atual: {get_theme_tokens()["name"]}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with topo_dir:
            if st.button("Light/Dark", key="theme_toggle_login", use_container_width=True):
                toggle_theme_mode()
                st.rerun()

        st.markdown(
            f"""
            <section class="hero-shell">
                <div class="hero-grid">
                    <div>
                        <div class="hero-kicker">Controle financeiro pessoal com visão de diretoria</div>
                        <h1 class="hero-title">Despesas ao Controle Total</h1>
                        <p class="hero-copy">
                            Organize despesas, acompanhe vencimentos, compare orçamento com execução
                            e gere relatórios claros para tomar decisões com mais velocidade e menos atrito.
                        </p>
                        <div class="chip-row">
                            <span class="chip">Alertas automáticos</span>
                            <span class="chip">Planejamento mensal</span>
                            <span class="chip">Exportação em Excel</span>
                            <span class="chip">Painel executivo</span>
                        </div>
                    </div>
                    <div class="hero-panel">
                        <div class="panel-stat">
                            <div class="panel-label">Perfil padrão</div>
                            <div class="panel-value">Alta Direção</div>
                        </div>
                        <div class="panel-stat">
                            <div class="panel-label">Renda mensal base</div>
                            <div class="panel-value">{escape(renda_padrao)}</div>
                        </div>
                        <div class="panel-stat">
                            <div class="panel-label">Limite mensal sugerido</div>
                            <div class="panel-value">{escape(limite_padrao)}</div>
                        </div>
                    </div>
                </div>
            </section>
            """,
            unsafe_allow_html=True,
        )

        with st.form("login_form"):
            st.markdown("### Entrar no sistema")
            col1, col2 = st.columns(2)
            with col1:
                usuario = st.text_input("Usuário", value="diretor")
            with col2:
                senha = st.text_input("Senha", type="password", value="123456")
            entrar = st.form_submit_button("Entrar no sistema")

        if entrar:
            if usuario == "diretor" and senha == "123456":
                st.session_state.logged_in = True
                st.success("Login realizado com sucesso.")
                st.rerun()
            else:
                st.error("Usuário ou senha inválidos.")
        st.stop()


apply_theme()
login_block()
refresh_alerts()


# ==============================
# SIDEBAR
# ==============================
st.sidebar.title("📊 Menu Executivo")
st.sidebar.caption(f"Tema atual: {get_theme_tokens()['name']}")
if st.sidebar.button("Light/Dark", key="theme_toggle_sidebar", use_container_width=True):
    toggle_theme_mode()
    st.rerun()

pages = [
    "Início",
    "Dashboard",
    "Cadastro do Usuário",
    "Categorias",
    "Lançar Despesa",
    "Controle Mensal",
    "Planejamento Financeiro",
    "Relatórios",
    "Alertas",
]
page = st.sidebar.radio("Navegação", pages)

if st.sidebar.button("Sair"):
    st.session_state.logged_in = False
    st.rerun()


# ==============================
# INÍCIO
# ==============================
def page_inicio():
    user = get_user()
    nome_usuario = escape(str(user["nome"])) if user is not None else "Diretor Executivo"
    objetivo = (
        escape(str(user["objetivo_financeiro"]))
        if user is not None and user["objetivo_financeiro"]
        else "Controlar gastos, preservar liquidez e ampliar a capacidade de investimento."
    )
    perfil = escape(str(user["perfil"])) if user is not None and user["perfil"] else "Alta Direção"

    resumo_df = read_df(
        """
        SELECT
            COUNT(*) AS total_despesas,
            COALESCE(SUM(valor), 0) AS gasto_total
        FROM despesas
        """
    )
    total_despesas = int(resumo_df.iloc[0]["total_despesas"]) if not resumo_df.empty else 0
    gasto_total = float(resumo_df.iloc[0]["gasto_total"]) if not resumo_df.empty else 0.0
    total_categorias = len(get_categories())
    total_alertas = len(read_df("SELECT id_alerta FROM alertas WHERE status = 'Ativo'"))
    total_orcamentos = len(read_df("SELECT id_orcamento FROM orcamentos"))

    st.markdown(
        f"""
        <section class="hero-shell">
            <div class="hero-grid">
                <div>
                    <div class="hero-kicker">{perfil}</div>
                    <h1 class="hero-title">Uma visão financeira mais clara para {nome_usuario}</h1>
                    <p class="hero-copy">
                        Este sistema consolida despesas pessoais, orçamento, vencimentos e relatórios
                        em uma única rotina visual, rápida e pronta para apoiar decisões do dia a dia.
                    </p>
                    <div class="chip-row">
                        <span class="chip">Orçamento vs realizado</span>
                        <span class="chip">Despesas recorrentes</span>
                        <span class="chip">Pendências e vencimentos</span>
                        <span class="chip">Análise por categoria</span>
                    </div>
                </div>
                <div class="hero-panel">
                    <div class="panel-stat">
                        <div class="panel-label">Objetivo financeiro</div>
                        <div class="panel-value">{objetivo}</div>
                    </div>
                    <div class="panel-stat">
                        <div class="panel-label">Base do sistema</div>
                        <div class="panel-value">Streamlit + SQLite</div>
                    </div>
                </div>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    r1, r2, r3, r4 = st.columns(4)
    with r1:
        render_summary_card("Despesas registradas", str(total_despesas), "Volume acumulado já lançado no sistema.")
    with r2:
        render_summary_card("Gasto acumulado", format_currency(gasto_total), "Total financeiro já consolidado na base.")
    with r3:
        render_summary_card("Alertas ativos", str(total_alertas), "Monitoramento de vencimentos e orçamento.")
    with r4:
        render_summary_card("Estrutura cadastrada", f"{total_categorias} categorias / {total_orcamentos} orçamentos", "Cobertura atual para análise e planejamento.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            """
            <div class="content-card">
                <div class="theme-badge">Desafio</div>
                <h3 class="section-title">O que costuma dificultar o controle</h3>
                <ul>
                    <li>Muitas despesas distribuídas ao longo do mês.</li>
                    <li>Diversos meios de pagamento e vencimentos espalhados.</li>
                    <li>Pouco tempo para consolidar tudo manualmente.</li>
                    <li>Baixa visibilidade sobre excessos antes do fechamento.</li>
                    <li>Dificuldade para perceber padrões de consumo rapidamente.</li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            """
            <div class="content-card">
                <div class="theme-badge">Resposta do sistema</div>
                <h3 class="section-title">Como a plataforma organiza a rotina</h3>
                <ul>
                    <li>Cadastro completo de despesas com classificação executiva.</li>
                    <li>Centralização em banco SQLite com navegação simples.</li>
                    <li>Dashboard com indicadores, gráficos e leitura rápida.</li>
                    <li>Planejamento financeiro por categoria e por mês.</li>
                    <li>Relatórios exportáveis para acompanhamento e prestação de contas.</li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("### Categorias de gastos monitoradas")
    categorias_txt = [
        "Alimentação",
        "Transporte",
        "Moradia",
        "Saúde",
        "Lazer",
        "Educação",
        "Viagens",
        "Compras Pessoais",
        "Assinaturas",
        "Contas Fixas",
        "Imprevistos",
    ]
    st.markdown(
        '<div class="content-card"><div class="categories-strip">'
        + "".join(f'<span class="chip">{escape(categoria)}</span>' for categoria in categorias_txt)
        + "</div></div>",
        unsafe_allow_html=True,
    )

    st.markdown("### Fluxo principal")
    e1, e2, e3 = st.columns(3)
    with e1:
        st.markdown(
            """
            <div class="stage-card">
                <div class="stage-label">1. Registrar</div>
                <h3 class="section-title">Entrada estruturada</h3>
                <p>Inclua descrição, categoria, meio de pagamento, prioridade, recorrência, parcelas e vencimento.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with e2:
        st.markdown(
            """
            <div class="stage-card">
                <div class="stage-label">2. Acompanhar</div>
                <h3 class="section-title">Leitura mensal</h3>
                <p>Compare planejado e realizado, acompanhe pendências e identifique rapidamente excessos por categoria.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with e3:
        st.markdown(
            """
            <div class="stage-card">
                <div class="stage-label">3. Decidir</div>
                <h3 class="section-title">Relatórios executivos</h3>
                <p>Transforme os dados em visão prática para revisão de hábitos, ajustes de teto e decisões financeiras melhores.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ==============================
# DASHBOARD
# ==============================
def page_dashboard():
    st.title("📈 Dashboard Executivo")
    st.caption("Visão gerencial dos gastos pessoais para apoio à tomada de decisão.")

    mes_atual = st.selectbox(
        "Selecione o mês de referência",
        options=sorted(
            list(
                set(
                    read_df("SELECT substr(data, 1, 7) AS mes FROM despesas")["mes"].dropna().tolist()
                    + [date.today().strftime("%Y-%m")]
                )
            ),
            reverse=True,
        ),
    )

    despesas = read_df(
        "SELECT * FROM despesas WHERE substr(data, 1, 7) = ? ORDER BY data DESC",
        [mes_atual],
    )
    update_budget_realizado(mes_atual)
    orcamentos = read_df("SELECT * FROM orcamentos WHERE mes = ? ORDER BY categoria", [mes_atual])

    total_gasto = float(despesas["valor"].sum()) if not despesas.empty else 0.0
    qtd_despesas = int(len(despesas))
    fixos = float(despesas.loc[despesas["tipo_gasto"] == "Fixo", "valor"].sum()) if not despesas.empty else 0.0
    variaveis = float(despesas.loc[despesas["tipo_gasto"] == "Variável", "valor"].sum()) if not despesas.empty else 0.0
    pendentes = float(despesas.loc[despesas["status_pagamento"] == "Pendente", "valor"].sum()) if not despesas.empty else 0.0
    vencidas = float(despesas.loc[despesas["status_pagamento"] == "Vencido", "valor"].sum()) if not despesas.empty else 0.0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total gasto", format_currency(total_gasto))
    c2.metric("Qtd. despesas", qtd_despesas)
    c3.metric("Gastos fixos", format_currency(fixos))
    c4.metric("Gastos variáveis", format_currency(variaveis))
    c5.metric("Pendentes", format_currency(pendentes))
    c6.metric("Vencidas", format_currency(vencidas))

    if despesas.empty:
        st.info("Ainda não há despesas lançadas para o mês selecionado.")
        return

    col1, col2 = st.columns(2)

    with col1:
        por_categoria = despesas.groupby("categoria", as_index=False)["valor"].sum().sort_values("valor", ascending=False)
        fig_pizza = px.pie(
            por_categoria,
            names="categoria",
            values="valor",
            title="Distribuição dos gastos por categoria",
            hole=0.45,
        )
        st.plotly_chart(fig_pizza, use_container_width=True)

        fig_barras = px.bar(
            por_categoria,
            x="categoria",
            y="valor",
            title="Comparação entre categorias",
            text_auto=True,
        )
        st.plotly_chart(fig_barras, use_container_width=True)

    with col2:
        por_mes = read_df(
            """
            SELECT substr(data, 1, 7) AS mes, SUM(valor) AS total
            FROM despesas
            GROUP BY substr(data, 1, 7)
            ORDER BY mes
            """
        )
        if not por_mes.empty:
            fig_linha = px.line(
                por_mes,
                x="mes",
                y="total",
                markers=True,
                title="Evolução mensal dos gastos",
            )
            st.plotly_chart(fig_linha, use_container_width=True)

        por_pagamento = despesas.groupby("meio_pagamento", as_index=False)["valor"].sum().sort_values("valor", ascending=False)
        fig_pagamento = px.bar(
            por_pagamento,
            x="meio_pagamento",
            y="valor",
            title="Gastos por meio de pagamento",
            text_auto=True,
        )
        st.plotly_chart(fig_pagamento, use_container_width=True)

    st.subheader("Planejado x Realizado")
    st.markdown(
        "Exemplo: Alimentação planejada em **R$ 1.500,00**, realizada em **R$ 1.980,00** e diferença de **R$ 480,00 acima do previsto**."
    )
    if not orcamentos.empty:
        comp = orcamentos[["categoria", "valor_planejado", "valor_realizado"]].copy()
        comp_melt = comp.melt(id_vars="categoria", var_name="tipo", value_name="valor")
        fig_orc = px.bar(
            comp_melt,
            x="categoria",
            y="valor",
            color="tipo",
            barmode="group",
            title="Orçamento previsto x gasto realizado",
            text_auto=True,
        )
        st.plotly_chart(fig_orc, use_container_width=True)
        st.dataframe(
            orcamentos[["categoria", "valor_planejado", "valor_realizado", "diferenca"]],
            use_container_width=True,
        )
    else:
        st.info("Ainda não há orçamento cadastrado para este mês.")


# ==============================
# CADASTRO DE USUÁRIO
# ==============================
def page_usuario():
    st.title("👤 Cadastro do Usuário")
    user = get_user()

    with st.form("form_usuario"):
        nome = st.text_input("Nome", value=user["nome"] if user is not None else "")
        perfil = st.text_input("Perfil", value=user["perfil"] if user is not None else "Alta Direção")
        renda_mensal = st.number_input(
            "Renda mensal (R$)",
            min_value=0.0,
            value=float(user["renda_mensal"]) if user is not None else 0.0,
            step=100.0,
        )
        limite_orcamento = st.number_input(
            "Limite de orçamento mensal (R$)",
            min_value=0.0,
            value=float(user["limite_orcamento"]) if user is not None else 0.0,
            step=100.0,
        )
        objetivo = st.text_area(
            "Objetivo financeiro",
            value=user["objetivo_financeiro"] if user is not None else "",
            height=100,
        )
        salvar = st.form_submit_button("Salvar dados")

    if salvar:
        if user is not None:
            run_query(
                """
                UPDATE usuario
                SET nome = ?, perfil = ?, renda_mensal = ?, limite_orcamento = ?, objetivo_financeiro = ?
                WHERE id_usuario = ?
                """,
                [nome, perfil, renda_mensal, limite_orcamento, objetivo, int(user["id_usuario"])],
            )
        else:
            run_query(
                """
                INSERT INTO usuario (nome, perfil, renda_mensal, limite_orcamento, objetivo_financeiro)
                VALUES (?, ?, ?, ?, ?)
                """,
                [nome, perfil, renda_mensal, limite_orcamento, objetivo],
            )
        st.success("Cadastro do usuário atualizado com sucesso.")
        st.rerun()


# ==============================
# CATEGORIAS
# ==============================
def page_categorias():
    st.title("🗂️ Cadastro de Categorias")

    with st.form("form_categoria"):
        nome_categoria = st.text_input("Nome da categoria")
        tipo_categoria = st.selectbox("Tipo da categoria", ["Fixo", "Variável"])
        subcategoria_padrao = st.text_input("Subcategoria padrão")
        salvar_categoria = st.form_submit_button("Adicionar categoria")

    if salvar_categoria:
        if nome_categoria.strip():
            try:
                run_query(
                    """
                    INSERT INTO categorias (nome_categoria, tipo_categoria, subcategoria_padrao)
                    VALUES (?, ?, ?)
                    """,
                    [nome_categoria.strip(), tipo_categoria, subcategoria_padrao.strip()],
                )
                st.success("Categoria cadastrada com sucesso.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.warning("Essa categoria já existe.")
        else:
            st.error("Informe o nome da categoria.")

    st.subheader("Categorias cadastradas")
    categorias_df = read_df("SELECT * FROM categorias ORDER BY nome_categoria")
    st.dataframe(categorias_df, use_container_width=True)


# ==============================
# LANÇAR DESPESA
# ==============================
def page_lancar_despesa():
    st.title("💳 Lançamento de Despesas")

    categorias = get_categories()
    if not categorias:
        st.warning("Cadastre ao menos uma categoria antes de lançar despesas.")
        return

    with st.form("form_despesa"):
        c1, c2, c3 = st.columns(3)
        with c1:
            data_despesa = st.date_input("Data da despesa", value=date.today())
            descricao = st.text_input("Descrição")
            categoria = st.selectbox("Categoria", categorias)
            subcategoria = st.text_input("Subcategoria")
            valor_total = st.number_input("Valor total da compra (R$)", min_value=0.0, step=0.01)

        with c2:
            meio_pagamento = st.selectbox(
                "Meio de pagamento",
                ["Cartão de Crédito", "Cartão de Débito", "PIX", "Transferência", "Dinheiro", "Boleto"],
            )
            tipo_gasto = st.selectbox("Tipo do gasto", ["Fixo", "Variável"])
            prioridade = st.selectbox("Prioridade", ["Alta", "Média", "Baixa"])
            recorrente = st.checkbox("Despesa recorrente")
            assinatura_automatica = st.checkbox("Assinatura automática")
            status_pagamento = st.selectbox("Status do pagamento", ["Pago", "Pendente", "Vencido"])

        with c3:
            parcelado = st.checkbox("Compra parcelada")
            total_parcelas = st.number_input("Quantidade de parcelas", min_value=1, value=1, step=1)
            parcela_atual = st.number_input("Parcela atual", min_value=1, value=1, step=1)
            vencimento = st.date_input("Data de vencimento", value=date.today())
            observacao = st.text_area("Observações", height=110)

        salvar_despesa = st.form_submit_button("Salvar despesa")

    if salvar_despesa:
        if not descricao.strip():
            st.error("Informe a descrição da despesa.")
            return

        total_parcelas = int(total_parcelas) if parcelado else 1
        parcela_atual = int(parcela_atual) if parcelado else 1
        valor_parcela = float(valor_total) / total_parcelas if total_parcelas > 0 else float(valor_total)

        if parcela_atual > total_parcelas:
            st.error("A parcela atual não pode ser maior que o total de parcelas.")
            return

        observacao_final = observacao.strip()
        if assinatura_automatica:
            observacao_final = (observacao_final + " | Assinatura automática").strip(" |")

        run_query(
            """
            INSERT INTO despesas (
                data, descricao, categoria, subcategoria, valor, meio_pagamento,
                tipo_gasto, prioridade, recorrente, parcela_atual, total_parcelas,
                vencimento, status_pagamento, observacao
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                data_despesa.strftime("%Y-%m-%d"),
                descricao.strip(),
                categoria,
                subcategoria.strip(),
                valor_parcela,
                meio_pagamento,
                tipo_gasto,
                prioridade,
                1 if recorrente or assinatura_automatica else 0,
                parcela_atual,
                total_parcelas,
                vencimento.strftime("%Y-%m-%d"),
                status_pagamento,
                observacao_final,
            ],
        )
        refresh_alerts()
        st.success(
            f"Despesa cadastrada com sucesso. Valor por parcela: {format_currency(valor_parcela)}"
        )
        st.rerun()


# ==============================
# CONTROLE MENSAL
# ==============================
def page_controle_mensal():
    st.title("📅 Controle Mensal de Despesas")

    despesas = read_df("SELECT * FROM despesas ORDER BY data DESC, id_despesa DESC")
    if despesas.empty:
        st.info("Nenhuma despesa cadastrada ainda.")
        return

    despesas["mes"] = despesas["data"].str.slice(0, 7)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        filtro_mes = st.selectbox("Mês", ["Todos"] + sorted(despesas["mes"].unique().tolist(), reverse=True))
    with c2:
        filtro_categoria = st.selectbox("Categoria", ["Todas"] + sorted(despesas["categoria"].unique().tolist()))
    with c3:
        filtro_status = st.selectbox("Status", ["Todos"] + sorted(despesas["status_pagamento"].unique().tolist()))
    with c4:
        filtro_pagamento = st.selectbox("Meio de pagamento", ["Todos"] + sorted(despesas["meio_pagamento"].unique().tolist()))

    filtrado = despesas.copy()
    if filtro_mes != "Todos":
        filtrado = filtrado[filtrado["mes"] == filtro_mes]
    if filtro_categoria != "Todas":
        filtrado = filtrado[filtrado["categoria"] == filtro_categoria]
    if filtro_status != "Todos":
        filtrado = filtrado[filtrado["status_pagamento"] == filtro_status]
    if filtro_pagamento != "Todos":
        filtrado = filtrado[filtrado["meio_pagamento"] == filtro_pagamento]

    total_filtrado = float(filtrado["valor"].sum()) if not filtrado.empty else 0.0
    st.metric("Total filtrado", format_currency(total_filtrado))

    st.dataframe(filtrado, use_container_width=True)

    st.subheader("Excluir despesa")
    ids = filtrado["id_despesa"].tolist()
    if ids:
        id_excluir = st.selectbox("Selecione o ID da despesa para excluir", ids)
        if st.button("Excluir despesa selecionada"):
            run_query("DELETE FROM despesas WHERE id_despesa = ?", [int(id_excluir)])
            refresh_alerts()
            st.success("Despesa excluída com sucesso.")
            st.rerun()


# ==============================
# PLANEJAMENTO FINANCEIRO
# ==============================
def page_planejamento():
    st.title("🎯 Planejamento Financeiro")

    categorias = get_categories()
    mes_ref = st.text_input("Mês de referência (AAAA-MM)", value=date.today().strftime("%Y-%m"))

    with st.form("form_orcamento"):
        categoria = st.selectbox("Categoria para orçamento", categorias)
        valor_planejado = st.number_input("Valor planejado (R$)", min_value=0.0, step=0.01)
        salvar_orc = st.form_submit_button("Salvar orçamento")

    if salvar_orc:
        try:
            run_query(
                """
                INSERT INTO orcamentos (mes, categoria, valor_planejado, valor_realizado, diferenca)
                VALUES (?, ?, ?, 0, 0)
                """,
                [mes_ref, categoria, valor_planejado],
            )
            st.success("Orçamento cadastrado com sucesso.")
        except sqlite3.IntegrityError:
            run_query(
                """
                UPDATE orcamentos
                SET valor_planejado = ?
                WHERE mes = ? AND categoria = ?
                """,
                [valor_planejado, mes_ref, categoria],
            )
            st.success("Orçamento atualizado com sucesso.")
        update_budget_realizado(mes_ref)
        refresh_alerts()
        st.rerun()

    update_budget_realizado(mes_ref)
    orc_df = read_df("SELECT * FROM orcamentos WHERE mes = ? ORDER BY categoria", [mes_ref])
    if orc_df.empty:
        st.info("Nenhum orçamento cadastrado para este mês.")
        return

    orc_df["status"] = orc_df.apply(
        lambda x: "Dentro do limite" if float(x["diferenca"]) >= 0 else "Acima do previsto",
        axis=1,
    )
    st.dataframe(orc_df, use_container_width=True)


# ==============================
# RELATÓRIOS
# ==============================
def page_relatorios():
    st.title("🧾 Relatórios Gerenciais")

    despesas = read_df("SELECT * FROM despesas ORDER BY data DESC")
    orc = read_df("SELECT * FROM orcamentos ORDER BY mes DESC, categoria")
    alertas_df = read_df("SELECT * FROM alertas ORDER BY id_alerta DESC")

    if despesas.empty:
        st.info("Cadastre despesas para gerar relatórios.")
        return

    rel_mensal = read_df(
        """
        SELECT substr(data, 1, 7) AS mes, SUM(valor) AS total_gasto, COUNT(*) AS quantidade
        FROM despesas
        GROUP BY substr(data, 1, 7)
        ORDER BY mes DESC
        """
    )
    rel_categoria = read_df(
        """
        SELECT categoria, SUM(valor) AS total_gasto, COUNT(*) AS quantidade
        FROM despesas
        GROUP BY categoria
        ORDER BY total_gasto DESC
        """
    )
    rel_pagamento = read_df(
        """
        SELECT meio_pagamento, SUM(valor) AS total_gasto, COUNT(*) AS quantidade
        FROM despesas
        GROUP BY meio_pagamento
        ORDER BY total_gasto DESC
        """
    )
    rel_pendencias = read_df(
        """
        SELECT data, descricao, categoria, valor, vencimento, status_pagamento
        FROM despesas
        WHERE status_pagamento IN ('Pendente', 'Vencido')
        ORDER BY vencimento ASC
        """
    )
    rel_recorrentes = read_df(
        """
        SELECT data, descricao, categoria, valor, meio_pagamento, parcela_atual, total_parcelas, vencimento, observacao
        FROM despesas
        WHERE recorrente = 1 OR total_parcelas > 1 OR observacao LIKE '%Assinatura automática%'
        ORDER BY data DESC
        """
    )
    rel_comparacao_meses = read_df(
        """
        SELECT substr(data, 1, 7) AS mes,
               SUM(valor) AS total_gasto,
               SUM(CASE WHEN tipo_gasto = 'Fixo' THEN valor ELSE 0 END) AS gastos_fixos,
               SUM(CASE WHEN tipo_gasto = 'Variável' THEN valor ELSE 0 END) AS gastos_variaveis
        FROM despesas
        GROUP BY substr(data, 1, 7)
        ORDER BY mes DESC
        """
    )

    aba1, aba2, aba3, aba4, aba5, aba6 = st.tabs([
        "Relatório Mensal",
        "Por Categoria",
        "Por Pagamento",
        "Pendências",
        "Recorrentes e Parceladas",
        "Comparação entre Meses",
    ])

    with aba1:
        st.dataframe(rel_mensal, use_container_width=True)
    with aba2:
        st.dataframe(rel_categoria, use_container_width=True)
    with aba3:
        st.dataframe(rel_pagamento, use_container_width=True)
    with aba4:
        st.dataframe(rel_pendencias, use_container_width=True)
    with aba5:
        st.dataframe(rel_recorrentes, use_container_width=True)
    with aba6:
        st.dataframe(rel_comparacao_meses, use_container_width=True)

    excel_file = export_excel(
        {
            "Despesas": despesas,
            "Orcamentos": orc,
            "Alertas": alertas_df,
            "Relatorio_Mensal": rel_mensal,
            "Relatorio_Categoria": rel_categoria,
            "Relatorio_Pagamento": rel_pagamento,
            "Pendencias": rel_pendencias,
            "Recorrentes_Parceladas": rel_recorrentes,
            "Comparacao_Meses": rel_comparacao_meses,
        }
    )

    st.download_button(
        label="⬇️ Baixar relatórios em Excel",
        data=excel_file,
        file_name="relatorios_gastos_pessoais.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ==============================
# ALERTAS
# ==============================
def page_alertas():
    st.title("🚨 Alertas e Monitoramento")

    refresh_alerts()
    alertas_df = read_df("SELECT * FROM alertas ORDER BY id_alerta DESC")
    if alertas_df.empty:
        st.success("Nenhum alerta ativo no momento.")
        return

    st.dataframe(alertas_df, use_container_width=True)

    for _, alerta in alertas_df.iterrows():
        tipo = alerta["tipo_alerta"]
        mensagem = alerta["descricao"]
        if tipo == "Despesa Vencida":
            st.error(mensagem)
        elif tipo == "Vencimento Próximo":
            st.warning(mensagem)
        else:
            st.info(mensagem)


# ==============================
# EXECUÇÃO DAS PÁGINAS
# ==============================
if page == "Início":
    page_inicio()
elif page == "Dashboard":
    page_dashboard()
elif page == "Cadastro do Usuário":
    page_usuario()
elif page == "Categorias":
    page_categorias()
elif page == "Lançar Despesa":
    page_lancar_despesa()
elif page == "Controle Mensal":
    page_controle_mensal()
elif page == "Planejamento Financeiro":
    page_planejamento()
elif page == "Relatórios":
    page_relatorios()
elif page == "Alertas":
    page_alertas()


# ==============================
# RODAPÉ
# ==============================
st.markdown("---")
st.caption(
    "Sistema Inteligente de Gestão de Gastos Pessoais para Alta Direção | Desenvolvido em Streamlit + SQLite"
)
