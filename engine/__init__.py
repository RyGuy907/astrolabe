"""Pure-Python astronomy engine.

Imports nothing web-related. The only I/O side effect permitted anywhere in this
package is reading (and, on first run, populating) the ephemeris cache.

All datetimes crossing a module boundary are timezone-aware UTC. See
`engine.timeutil` for the enforcement helpers.
"""
