"""Runtime composition modules with no package-import side effects.

Callers import the concrete ``premarket``, ``research`` or ``coordinator`` module so
API schema generation does not load source configuration merely by importing this
package.
"""
