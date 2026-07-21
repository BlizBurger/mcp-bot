"""Dashboard local interactif (Streamlit, thème noir) — lecture seule + backtest.

Lancement : streamlit run dashboard.py

Onglets : Statut · Stratégies (descriptions) · Backtest & comparatif · Alertes.
Le bot n'exécute jamais d'ordre — décision et exécution 100% manuelles.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from smc import WARNINGS
from smc.config import load_config
from smc.db import connect, recent_alerts
from smc.news import load_news
from smc.strategies import STRATEGIES
from smc.strategies.descriptions import STRATEGY_INFO

st.set_page_config(page_title="SMC Scanner", page_icon="📡", layout="wide")

# Thème noir
st.markdown("""<style>
.stApp { background: #0a0a0f; }
h1, h2, h3, p, span, label, div { color: #d8d8e0; }
.stat-card { background:#14141f; border:1px solid #26263a; border-radius:12px;
             padding:1rem 1.3rem; }
.stat-card b { font-size:1.5rem; color:#fff; display:block; }
.strat { background:#12121c; border:1px solid #26263a; border-radius:12px;
         padding:1rem 1.3rem; margin-bottom:1rem; }
.strat h3 { color:#fff; margin:0 0 .3rem 0; }
.badge { display:inline-block; background:#17172a; color:#6c8cff;
         border-radius:6px; padding:2px 8px; font-size:.8rem; margin-right:.4rem;}
</style>""", unsafe_allow_html=True)

cfg = load_config()

st.title("📡 SMC Scanner — tableau de bord")
st.caption("Outil d'ALERTE uniquement — aucune exécution d'ordre. "
           "Décision et passage d'ordre 100% manuels.")

tab_status, tab_strat, tab_bt, tab_alerts = st.tabs(
    ["🟢 Statut", "📚 Stratégies", "🧪 Backtest & comparatif", "🔔 Alertes"])

# --------------------------------------------------------------- Statut
with tab_status:
    c1, c2, c3 = st.columns(3)
    status_path = Path(cfg["paths"]["status"])
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(status["updated_at"])
        age = (datetime.now() - updated).total_seconds() / 60
        fresh = age < cfg["scanner"]["interval_seconds"] / 60 * 2 + 2
        c1.markdown(f"<div class='stat-card'><b>{'🟢 Actif' if fresh else '🔴 Inactif'}</b>"
                    f"dernier scan {updated:%H:%M:%S} (il y a {age:.0f} min)</div>",
                    unsafe_allow_html=True)
        c2.markdown(f"<div class='stat-card'><b>{'✅' if status.get('mt5_connected') else '❌'}</b>"
                    f"connexion MT5</div>", unsafe_allow_html=True)
        c3.markdown(f"<div class='stat-card'><b>{len(status.get('pairs', []))}</b>"
                    f"paires surveillées</div>", unsafe_allow_html=True)
    else:
        st.info("Le scanner n'a pas encore tourné (aucun fichier de statut).")

    st.subheader("Stratégie active")
    st.markdown(f"**{cfg.get('strategy_name')}** — "
                f"{STRATEGY_INFO.get(cfg.get('strategy_name'), {}).get('titre', '')}")

    st.subheader("News du jour")
    events = load_news(cfg["news"]["file"])
    if events:
        st.dataframe(pd.DataFrame(
            [{"Heure": e.time.strftime("%H:%M"), "Devise": e.currency,
              "Événement": e.name, "Impact": e.impact,
              "Blackout": "🚫" if e.is_high else ""} for e in events]),
            use_container_width=True, hide_index=True)
    else:
        st.warning("Aucune news chargée — filtre news INACTIF aujourd'hui.")

    with st.expander("⚠️ Limites connues de l'outil"):
        for w in WARNINGS:
            st.warning(w)

# --------------------------------------------------------------- Stratégies
with tab_strat:
    st.subheader("Les stratégies du labo")
    st.caption("Chaque stratégie est testable et comparable sur le même moteur, "
               "avec validation out-of-sample.")
    for name in STRATEGIES:
        info = STRATEGY_INFO.get(name)
        if not info:
            continue
        active = " ✅ (active)" if name == cfg.get("strategy_name") else ""
        steps = "".join(f"<li>{s}</li>" for s in info["fonctionnement"])
        st.markdown(
            f"<div class='strat'><h3>{info['titre']}{active}</h3>"
            f"<span class='badge'>{name}</span>"
            f"<span class='badge'>{info['famille']}</span>"
            f"<span class='badge'>edge : {info['edge']}</span>"
            f"<p>{info['resume']}</p>"
            f"<ol>{steps}</ol>"
            f"<p style='color:#8a8aa0'>Paramètres : <code>{info['params']}</code></p>"
            f"</div>", unsafe_allow_html=True)

# --------------------------------------------------------------- Backtest
with tab_bt:
    st.subheader("Lancer un backtest / comparatif")
    col1, col2, col3 = st.columns(3)
    days = col1.number_input("Jours d'historique", 30, 730, cfg["backtest"]["days"])
    strat = col2.selectbox("Stratégie", list(STRATEGIES),
                           index=list(STRATEGIES).index(cfg.get("strategy_name", "sweep_bos")))
    mode = col3.selectbox("Mode", ["Backtest simple", "Comparer les stratégies",
                                   "Balayer les R:R"])
    split = st.checkbox("Validation out-of-sample (--split 0.75)", value=True)

    if st.button("▶️ Lancer", type="primary"):
        cmd = [sys.executable, "-m", "smc.backtest", "--days", str(days),
               "--strategy", strat]
        if mode == "Comparer les stratégies":
            cmd.append("--compare-strategies")
        elif mode == "Balayer les R:R":
            cmd.append("--rr-sweep")
        if split and mode != "Backtest simple":
            cmd += ["--split", "0.75"]
        with st.spinner("Backtest en cours (nécessite MT5 ouvert)..."):
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  cwd=Path(__file__).parent)
        if proc.returncode == 0:
            st.success("Terminé.")
            st.code(proc.stdout[-3000:] or "(voir le rapport)")
        else:
            st.error(f"Échec :\n```\n{proc.stderr[-2000:]}\n```")

    latest = Path(cfg["paths"]["reports"]) / "latest.html"
    if latest.exists():
        st.caption(f"Dernier rapport : "
                   f"{datetime.fromtimestamp(latest.stat().st_mtime):%Y-%m-%d %H:%M}")
        st.components.v1.html(latest.read_text(encoding="utf-8"),
                              height=900, scrolling=True)

    # derniers CSV de comparatif
    reps = sorted(Path(cfg["paths"]["reports"]).glob("*.csv"),
                  key=lambda p: p.stat().st_mtime, reverse=True) \
        if Path(cfg["paths"]["reports"]).exists() else []
    comps = [p for p in reps if p.name.startswith(("strategies_", "rr_sweep_", "compare_"))]
    if comps:
        st.subheader("Dernier comparatif")
        st.dataframe(pd.read_csv(comps[0]), use_container_width=True, hide_index=True)

# --------------------------------------------------------------- Alertes
with tab_alerts:
    st.subheader("Derniers setups détectés")
    db_path = Path(cfg["paths"]["db"])
    if db_path.exists():
        conn = connect(str(db_path))
        rows = recent_alerts(conn, limit=100)
        conn.close()
        if rows:
            df = pd.DataFrame([dict(r) for r in rows])
            df["alerted"] = df["alerted"].map({1: "📨 envoyé", 0: "stocké"})
            df["swept"] = df["swept"].map({1: "oui", 0: "non"})
            cols = [c for c in ["created_at", "pair", "direction", "strategy",
                                "zone_kind", "swept", "score", "entry", "sl", "tp",
                                "rr", "alerted"] if c in df.columns]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)
        else:
            st.info("Aucun setup en base pour l'instant.")
    else:
        st.info("Base non créée — elle apparaîtra au premier setup détecté.")
