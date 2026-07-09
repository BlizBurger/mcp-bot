"""Dashboard local Streamlit — lecture seule + relance de backtest.

Lancement : streamlit run dashboard.py

Affiche : statut du scanner, news du jour, dernières alertes (SQLite),
rapport de backtest, et les LIMITES CONNUES de l'outil (en permanence).
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

st.set_page_config(page_title="SMC/AMD Scanner", page_icon="📡", layout="wide")
cfg = load_config()

st.title("📡 SMC/AMD Scanner — dashboard")
st.caption("Outil d'ALERTE uniquement — aucune exécution d'ordre. "
           "La décision de trader reste 100% manuelle.")

with st.expander("⚠️ Limites connues de l'outil (à relire régulièrement)",
                 expanded=False):
    for w in WARNINGS:
        st.warning(w)

col_status, col_news = st.columns(2)

# ---- Statut du scanner ----------------------------------------------------
with col_status:
    st.subheader("Statut du scanner")
    status_path = Path(cfg["paths"]["status"])
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(status["updated_at"])
        age_min = (datetime.now() - updated).total_seconds() / 60
        fresh = age_min < cfg["scanner"]["interval_seconds"] / 60 * 2 + 2
        st.metric("Dernier scan", updated.strftime("%H:%M:%S"),
                  delta=f"il y a {age_min:.0f} min",
                  delta_color="normal" if fresh else "inverse")
        if not fresh:
            st.error("Le scanner semble arrêté (statut trop ancien) — "
                     "vérifier le process et les logs.")
        st.write("MT5 connecté :", "✅" if status.get("mt5_connected") else "❌")
        st.write("Paires :", ", ".join(status.get("pairs", [])))
    else:
        st.info("Aucun fichier de statut — le scanner n'a pas encore tourné.")

# ---- News du jour ----------------------------------------------------------
with col_news:
    st.subheader("News du jour (fichier manuel)")
    events = load_news(cfg["news"]["file"])
    if events:
        st.dataframe(pd.DataFrame(
            [{"Heure": e.time.strftime("%H:%M"), "Devise": e.currency,
              "Événement": e.name, "Impact": e.impact,
              "Blackout": "🚫" if e.is_high else ""} for e in events]),
            use_container_width=True, hide_index=True)
    else:
        st.error("Aucune news chargée — le filtre news est INACTIF aujourd'hui. "
                 f"Remplir {Path(cfg['news']['file']).name} chaque matin.")

# ---- Dernières alertes ------------------------------------------------------
st.subheader("Derniers setups détectés (base SQLite)")
db_path = Path(cfg["paths"]["db"])
if db_path.exists():
    conn = connect(str(db_path))
    rows = recent_alerts(conn, limit=50)
    conn.close()
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        df["alerted"] = df["alerted"].map({1: "📨 envoyé", 0: "stocké (quota 1/jour)"})
        df["swept"] = df["swept"].map({1: "oui", 0: "non"})
        st.dataframe(df[["created_at", "pair", "direction", "zone_kind", "swept",
                         "entry", "sl", "tp", "rr", "alerted", "comment"]],
                     use_container_width=True, hide_index=True)
    else:
        st.info("Aucun setup en base pour l'instant.")
else:
    st.info("Base non créée — elle apparaîtra au premier setup détecté.")

# ---- Backtest ---------------------------------------------------------------
st.subheader("Backtest")
col_btn, col_days = st.columns([1, 1])
days = col_days.number_input("Jours d'historique", 10, 365,
                             cfg["backtest"]["days"])
if col_btn.button("▶️ Relancer un backtest", type="primary"):
    with st.spinner(f"Backtest {days} jours en cours (nécessite MT5)..."):
        proc = subprocess.run(
            [sys.executable, "-m", "smc.backtest", "--days", str(days)],
            capture_output=True, text=True, cwd=Path(__file__).parent)
    if proc.returncode == 0:
        st.success("Backtest terminé.")
    else:
        st.error(f"Échec du backtest :\n```\n{proc.stderr[-2000:]}\n```")

latest = Path(cfg["paths"]["reports"]) / "latest.html"
if latest.exists():
    st.caption(f"Dernier rapport : {datetime.fromtimestamp(latest.stat().st_mtime):%Y-%m-%d %H:%M}")
    st.components.v1.html(latest.read_text(encoding="utf-8"),
                          height=900, scrolling=True)
else:
    st.info("Aucun rapport de backtest généré pour l'instant.")
