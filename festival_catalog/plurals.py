# -*- coding: utf-8 -*-
"""CLDR 风格复数规则（教学实现，覆盖两台旧机所需的阿拉伯语/俄语/英语）。

数字 2 在两台旧机上走不同分支：
  * 阿拉伯语 ar -> two（双数）
  * 俄语     ru -> few（n%10 == 2 且 n%100 不在 12..14）
"""

from __future__ import annotations


def base_locale(locale: str) -> str:
    """ar-AE -> ar；ru-RU -> ru。"""
    return locale.split("-", 1)[0].lower()


def locale_chain(locale: str) -> list[str]:
    """地区 locale 的回退链：ar-AE -> ar -> en（包内默认语）。"""
    locale = locale.replace("_", "-")
    chain = [locale]
    base = base_locale(locale)
    if base != locale:
        chain.append(base)
    if "en" not in chain:
        chain.append("en")
    return chain


def _arabic(n: int) -> str:
    if n == 0:
        return "zero"
    if n == 1:
        return "one"
    if n == 2:
        return "two"
    remainder = n % 100
    if 3 <= remainder <= 10:
        return "few"
    if 11 <= remainder <= 99:
        return "many"
    return "other"


def _russian(n: int) -> str:
    tens = n % 10
    hundreds = n % 100
    if tens == 1 and hundreds != 11:
        return "one"
    if 2 <= tens <= 4 and not (12 <= hundreds <= 14):
        return "few"
    return "many"


def category(locale: str, n: int) -> str:
    """返回某 locale 下数量 n 对应的复数类别。"""
    base = base_locale(locale)
    if base == "ar":
        return _arabic(n)
    if base == "ru":
        return _russian(n)
    return "one" if n == 1 else "other"
