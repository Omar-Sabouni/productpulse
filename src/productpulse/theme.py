"""Local presentation layer for the ProductPulse analytics console."""

CONSOLE_STYLES = """
<style>
/* A report on continuous-feed computer paper: blue bars across the page,
   sprocket holes down both edges. Everything else stays out of the way. */
.stApp {
    --pp-ink: #1a1f24;
    --pp-muted: #5a6470;
    --pp-bar: #f2f6fc;
    --pp-rule: #b9c6d6;
    --pp-blue: #1f4e8c;
    --pp-paper: #ffffff;
    --pp-hole: radial-gradient(circle at 14px 50%, #e9ecef 4px, transparent 4.5px);
    color: var(--pp-ink);
}

.stApp [data-testid="stMain"] {
    background:
        var(--pp-hole) left top / 28px 24px repeat-y,
        var(--pp-hole) right top / 28px 24px repeat-y,
        repeating-linear-gradient(var(--pp-paper) 0 72px, var(--pp-bar) 72px 144px);
    background-attachment: local;
}


.stApp [data-testid="stHeader"] {
    background: transparent;
}

.stApp [data-testid="stMainBlockContainer"] {
    max-width: 1180px;
    padding: 3rem 3.5rem 4rem;
}

.stApp .pp-console-header {
    margin: 0 0 2px;
}

.stApp .pp-console-wordmark {
    margin: 0;
    padding: 0;
    color: var(--pp-ink);
    font-family: "IBM Plex Mono", monospace;
    font-size: 34px;
    font-weight: 600;
    line-height: 1.1;
}

.stApp [data-testid="stCaptionContainer"],
.stApp [data-testid="stCaptionContainer"] * {
    color: var(--pp-muted);
    font-size: 13px;
    opacity: 1;
}

/* Sidebar */
.stApp section[data-testid="stSidebar"] {
    border-right: 1px solid var(--pp-rule);
}

.stApp section[data-testid="stSidebar"] h2 {
    margin: 0 0 10px;
    padding: 0;
    font-size: 18px;
}

.stApp section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
    font-size: 12px;
    line-height: 1.5;
}

.stApp span[data-baseweb="tag"] {
    background: var(--pp-blue);
    color: var(--pp-paper);
    font-size: 12px;
}

/* Tabs read as section markers on the printout */
.stApp :is([data-baseweb="tab"], [role="tab"]) {
    padding: 6px 2px;
    margin-right: 22px;
    font-size: 15px;
}

.stApp :is([data-baseweb="tab-panel"], [role="tabpanel"]) h2 {
    margin: 0 0 4px;
    padding: 0;
    font-size: 22px;
}

/* Charts and tables sit on the paper as pasted-in white panels */
.stApp [data-testid="stVegaLiteChart"],
.stApp [data-testid="stDataFrame"] {
    margin: 6px 0 14px;
    background: var(--pp-paper);
    border: 1px solid #e1e7ef;
}

.stApp [data-testid="stVegaLiteChart"] {
    padding: 10px 10px 4px;
}

.stApp [data-testid="stExpander"] {
    background: var(--pp-paper);
}

@media (max-width: 700px) {
    .stApp [data-testid="stMain"] {
        background: repeating-linear-gradient(
            var(--pp-paper) 0 72px, var(--pp-bar) 72px 144px
        );
    }

    .stApp [data-testid="stMainBlockContainer"] {
        padding: 3rem 1rem 2.5rem;
    }

    .stApp .pp-console-wordmark {
        font-size: 28px;
    }
}
</style>
"""


CONSOLE_MASTHEAD = """
<header class="pp-console-header">
  <h1 class="pp-console-wordmark">ProductPulse</h1>
</header>
"""
