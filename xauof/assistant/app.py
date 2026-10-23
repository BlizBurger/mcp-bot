"""Tableau de bord de l'assistant manuel (lecture seule : aucun ordre n'est passé).

Lancement (MT5 ouvert, après « python -m xauof.assistant.update » le matin) :
    streamlit run xauof/assistant/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # streamlit lance le script hors du paquet

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from xauof.assistant import levels as lv  # noqa: E402
from xauof.assistant import mt5_live  # noqa: E402
from xauof.assistant.snapshot import ny_time, snapshot  # noqa: E402
from xauof.config import load_config  # noqa: E402
from xauof.news.calendar import load_calendar  # noqa: E402

BULL, BEAR, GOLD, BLUE, GREY = "#26a69a", "#ef5350", "#f0b429", "#58a6ff", "#8b949e"

st.set_page_config(page_title="XAUUSD OF — assistant", layout="wide")


@st.cache_resource
def mt5_connection():
    return mt5_live.connect()


def chart(snap: dict, xau: pd.DataFrame, tz: str) -> go.Figure:
    m5 = xau[["open", "high", "low", "close"]].resample("5min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    x = m5.index.tz_convert(tz)
    fig = go.Figure(go.Candlestick(x=x, open=m5["open"], high=m5["high"], low=m5["low"], close=m5["close"],
                                   increasing_line_color=BULL, decreasing_line_color=BEAR, name="XAUUSD M5"))
    lvl = snap.get("niveaux")
    if lvl:
        for name, val, color, dash in (("POC vol", lvl["poc_v"], GOLD, "solid"), ("VAH vol", lvl["vah_v"], GOLD, "dash"),
                                       ("VAL vol", lvl["val_v"], GOLD, "dash"), ("POC TPO", lvl["poc_t"], BLUE, "dot")):
            fig.add_hline(y=val, line_color=color, line_dash=dash, line_width=1,
                          annotation_text=f"{name} {val:.2f}", annotation_position="right")
        for v in lvl["lvn"]:
            fig.add_hline(y=v, line_color=GREY, line_width=1, annotation_text=f"LVN {v:.2f}",
                          annotation_position="left")
        for lo, hi in lvl["hvn"]:
            fig.add_hrect(y0=lo, y1=hi, fillcolor=GOLD, opacity=0.07, line_width=0)
        for lo, hi in lvl["singles"]:
            fig.add_hrect(y0=lo, y1=hi, fillcolor=BLUE, opacity=0.08, line_width=0)
    ib = snap.get("ib")
    if ib:
        fig.add_hrect(y0=ib["low"], y1=ib["high"], fillcolor=BLUE, opacity=0.05, line_width=0,
                      annotation_text="IB" + ("" if ib["definitive"] else " (en formation)"),
                      annotation_position="top left")
    for z in snap.get("zones_actives", []):
        fig.add_shape(type="rect", x0=z.active_from.tz_convert(tz), x1=snap["maintenant"].tz_convert(tz),
                      y0=z.low, y1=z.high, fillcolor=BULL if z.direction > 0 else BEAR, opacity=0.25, line_width=0)
    fig.update_layout(template="plotly_dark", height=620, margin=dict(l=10, r=90, t=10, b=10),
                      xaxis_rangeslider_visible=False, showlegend=False)
    return fig


@st.fragment(run_every="30s")
def live_view():
    cfg = load_config()
    tz = cfg["strategy"]["timezone"]
    now = pd.Timestamp.now(tz="UTC")
    day = now.tz_convert(tz).normalize().tz_localize(None)
    try:
        mt5 = mt5_connection()
        xau = mt5_live.xau_m1(mt5, cfg, ny_time(day, cfg["strategy"]["warmup_start"], tz), now + pd.Timedelta(minutes=1))
        acct = mt5_live.account(mt5, cfg, now)
    except Exception as e:  # MT5 fermé, déconnecté…
        st.error(f"MT5 indisponible : {e}")
        return
    snap = snapshot(cfg, xau, lv.load_levels(cfg, day), load_calendar(cfg), now, acct)
    if "erreur" in snap:
        st.warning(snap["erreur"])
        return

    p = snap["prix"]
    c = st.columns(5)
    c[0].metric("Bid", f"{p['bid']:.2f}")
    c[1].metric("Ask", f"{p['ask']:.2f}")
    c[2].metric("Spread", f"{p['spread_pts']:.0f} pts")
    c[3].metric("Heure New York", snap["heure_ny"])
    c[4].metric("Position / VA veille", snap.get("position_va", "—"))
    st.caption(snap["fenetre"])
    for a in snap["alertes"]:
        st.warning(a)

    left, right = st.columns([3, 1])
    left.plotly_chart(chart(snap, xau, tz), use_container_width=True)
    with right:
        st.subheader("Niveaux de la veille")
        if snap["niveaux"]:
            st.dataframe(pd.DataFrame(lv.level_lines(snap["niveaux"]), columns=["niveau", "prix"]),
                         hide_index=True, use_container_width=True)
            near = snap["niveau_proche"]
            st.caption(f"Le plus proche : {near[0]} {near[1]:.2f} ({p['bid'] - near[1]:+.2f} $)")
        st.subheader("IB du jour")
        ib = snap["ib"]
        st.write("—" if not ib else f"{ib['low']:.2f} – {ib['high']:.2f}" + ("" if ib["definitive"] else " (en formation)"))
        st.subheader("Zones FVG / OB actives")
        zs = snap["zones_actives"]
        if zs:
            st.dataframe(pd.DataFrame([{"zone": z.kind, "sens": "achat" if z.direction > 0 else "vente",
                                        "bas": round(z.low, 2), "haut": round(z.high, 2),
                                        "depuis": z.active_from.tz_convert(tz).strftime("%H:%M")} for z in zs]),
                         hide_index=True, use_container_width=True)
        else:
            st.write("aucune")
        st.subheader("Prochaines annonces USD")
        up = snap["prochaines_news"]
        if len(up):
            st.dataframe(pd.DataFrame({"heure NY": up.index.tz_convert(tz).strftime("%a %d %H:%M"),
                                       "annonce": up["event"].values}), hide_index=True, use_container_width=True)
        else:
            st.write("aucune connue" if snap["calendrier_ok"] else "calendrier non couvert")
        st.subheader("Compte FTMO")
        a = snap["compte"]
        if a:
            st.metric("Equity", f"{a['equity']:.2f} $", f"{-a['perte_jour']:+.2f} $ aujourd'hui")
            st.write(f"Avant arrêt interne : **{a['reste_arret_interne']:.2f} $**")
            st.write(f"Avant limite FTMO du jour : {a['reste_ftmo_jour']:.2f} $")
            st.write(f"Marge avant plancher FTMO : {a['marge_plancher_ftmo']:.2f} $")
    st.caption(f"Mis à jour {now.tz_convert(tz):%H:%M:%S} (New York) — rafraîchissement toutes les 30 s. "
               "Outil d'aide à la décision : aucune stratégie testée n'a montré d'edge statistique.")


st.title("XAUUSD OF — assistant de trading")
live_view()
