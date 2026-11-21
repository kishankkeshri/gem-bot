"""Gem-bot package.

IPv4 only: on the Pi's home line IPv6 to api.telegram.org hangs about 1 request in 3 (seen 2026-11-21:
20 s stalls, frozen buttons, users re-tapping -> duplicate PDFs), while IPv4 answers in ~0.5 s every time.
"""
import urllib3.util.connection as _conn

_conn.HAS_IPV6 = False  # urllib3 (under requests) then resolves IPv4 addresses only
