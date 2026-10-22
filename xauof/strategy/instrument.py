"""Spécifications XAUUSD du broker, taille de position et coûts estimés."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

from xauof.data import store


@dataclass(frozen=True)
class Instrument:
    contract_size: float
    tick_size: float
    tick_value: float          # devise du compte, par tick et par lot
    volume_min: float
    volume_step: float
    volume_max: float
    profit_currency: str
    point: float
    commission_rt: float       # USD par lot aller-retour (estimation)
    slippage: float            # $ par once
    verified: bool             # True si lu depuis MT5 (spec.json)

    @property
    def value_per_price(self) -> float:
        """USD gagnés ou perdus par lot pour 1,00 $ de variation du prix."""
        return self.tick_value / self.tick_size

    def pnl(self, direction: int, entry: float, exit_: float, lots: float) -> float:
        return direction * (exit_ - entry) * self.value_per_price * lots

    def round_lots_down(self, lots: float) -> float:
        steps = math.floor(lots / self.volume_step + 1e-9)
        return round(min(steps * self.volume_step, self.volume_max), 8)

    def loss_per_lot(self, entry: float, sl: float) -> float:
        """Perte par lot si le SL est touché : distance + slippage, plus la commission."""
        return (abs(entry - sl) + self.slippage) * self.value_per_price + self.commission_rt


def load_instrument(cfg: dict) -> Instrument:
    spec_path = store.data_dir(cfg) / "xauusd" / "spec.json"
    src, verified = dict(cfg["instrument"]), False
    if spec_path.exists():
        src.update(json.loads(spec_path.read_text(encoding="utf-8")))
        verified = True
    if src["profit_currency"] != cfg["account"]["currency"]:
        raise ValueError(f"devise de profit {src['profit_currency']} ≠ devise du compte : conversion non gérée en V1")
    return Instrument(
        contract_size=float(src["contract_size"]), tick_size=float(src["tick_size"]),
        tick_value=float(src["tick_value"]), volume_min=float(src["volume_min"]),
        volume_step=float(src["volume_step"]), volume_max=float(src["volume_max"]),
        profit_currency=src["profit_currency"], point=float(cfg["broker"]["point"]),
        commission_rt=float(cfg["costs"]["commission_per_lot_round_trip"]),
        slippage=float(cfg["costs"]["slippage_price"]), verified=verified,
    )


@dataclass
class Sizing:
    lots: float
    risk_usd: float            # perte estimée au SL (slippage + commission inclus)
    reward_usd: float          # gain estimé au TP (commission déduite)
    rr_net: float
    refused: str | None = None


def size_position(inst: Instrument, direction: int, entry: float, sl: float, tp: float,
                  budget_usd: float) -> Sizing:
    """Volume arrondi vers le bas pour que la perte au SL (coûts compris) reste ≤ budget."""
    if direction * (tp - entry) <= 0 or direction * (entry - sl) <= 0:
        return Sizing(0, 0, 0, 0, "SL ou TP du mauvais côté du prix d'entrée")
    per_lot_loss = inst.loss_per_lot(entry, sl)
    lots = inst.round_lots_down(budget_usd / per_lot_loss)
    if lots < inst.volume_min:
        return Sizing(0, 0, 0, 0, f"lot minimum {inst.volume_min} dépasse le budget "
                                  f"({inst.volume_min * per_lot_loss:.2f} > {budget_usd:.2f} USD)")
    risk = lots * per_lot_loss
    reward = lots * (abs(tp - entry) * inst.value_per_price - inst.commission_rt)
    return Sizing(lots, risk, reward, reward / risk if risk > 0 else 0.0)
