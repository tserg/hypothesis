# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Most of this work is copyright (C) 2013-2021 David R. MacIver
# (david@drmaciver.com), but it contains contributions by others. See
# CONTRIBUTING.rst for a full list of people who may hold copyright, and
# consult the git log if you need to determine who owns an individual
# contribution.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.
#
# END HEADER

"""
.. _codemods:

--------------------
hypothesis[codemods]
--------------------

This module provides codemods based on the :pypi:`LibCST` library, which can
both detect *and automatically fix* issues with code that uses Hypothesis,
including upgrading from deprecated features to our recommended style.

You can run the codemods via our CLI::

    $ hypothesis codemod --help
    Usage: hypothesis codemod [OPTIONS] PATH...

      `hypothesis codemod` refactors deprecated or inefficient code.

      It adapts `python -m libcst.tool`, removing many features and config
      options which are rarely relevant for this purpose.  If you need more
      control, we encourage you to use the libcst CLI directly; if not this one
      is easier.

      PATH is the file(s) or directories of files to format in place, or "-" to
      read from stdin and write to stdout.

    Options:
      -h, --help  Show this message and exit.

Alternatively you can use ``python -m libcst.tool``, which offers more control
at the cost of additional configuration (adding ``'hypothesis.extra'`` to the
``modules`` list in ``.libcst.codemod.yaml``) and `some issues on Windows
<https://github.com/Instagram/LibCST/issues/435>`__.

.. autofunction:: refactor
"""

import functools
import importlib
from inspect import Parameter, signature
from typing import List

import libcst as cst
import libcst.matchers as m
from libcst.codemod import VisitorBasedCodemodCommand


def refactor(code: str) -> str:
    """Update a source code string from deprecated to modern Hypothesis APIs.

    This may not fix *all* the deprecation warnings in your code, but we're
    confident that it will be easier than doing it all by hand.

    We recommend using the CLI, but if you want a Python function here it is.
    """
    context = cst.codemod.CodemodContext()
    mod = cst.parse_module(code)
    transforms: List[VisitorBasedCodemodCommand] = [
        HypothesisFixPositionalKeywonlyArgs(context),
        HypothesisFixComplexMinMagnitude(context),
    ]
    for transform in transforms:
        mod = transform.transform_module(mod)
    return mod.code


def match_qualname(name):
    # We use the metadata to get qualname instead of matching directly on function
    # name, because this handles some scope and "from x import y as z" issues.
    return m.MatchMetadataIfTrue(
        cst.metadata.QualifiedNameProvider,
        # If there are multiple possible qualnames, e.g. due to conditional imports,
        # be conservative.  Better to leave the user to fix a few things by hand than
        # to break their code while attempting to refactor it!
        lambda qualnames: all(n.name == name for n in qualnames),
    )


class HypothesisFixComplexMinMagnitude(VisitorBasedCodemodCommand):
    """Fix a deprecated min_magnitude=None argument for complex numbers::

        st.complex_numbers(min_magnitude=None) -> st.complex_numbers(min_magnitude=0)

    Note that this should be run *after* ``HypothesisFixPositionalKeywonlyArgs``,
    in order to handle ``st.complex_numbers(None)``.
    """

    DESCRIPTION = "Fix a deprecated min_magnitude=None argument for complex numbers."
    METADATA_DEPENDENCIES = (cst.metadata.QualifiedNameProvider,)

    @m.call_if_inside(
        m.Call(metadata=match_qualname("hypothesis.strategies.complex_numbers"))  # type: ignore
    )
    def leave_Arg(self, original_node, updated_node):
        if m.matches(
            updated_node, m.Arg(keyword=m.Name("min_magnitude"), value=m.Name("None"))
        ):
            return updated_node.with_changes(value=cst.Integer("0"))
        return updated_node


@functools.lru_cache()
def get_fn(import_path):
    mod, fn = import_path.rsplit(".", 1)
    return getattr(importlib.import_module(mod), fn)


class HypothesisFixPositionalKeywonlyArgs(VisitorBasedCodemodCommand):
    """Fix positional arguments for newly keyword-only parameters, e.g.::

        st.fractions(0, 1, 9) -> st.fractions(0, 1, max_denominator=9)

    Applies to a majority of our public API, since keyword-only parameters are
    great but we couldn't use them until after we dropped support for Python 2.
    """

    DESCRIPTION = "Fix positional arguments for newly keyword-only parameters."
    METADATA_DEPENDENCIES = (cst.metadata.QualifiedNameProvider,)

    kwonly_functions = (
        "hypothesis.target",
        "hypothesis.find",
        "hypothesis.extra.lark.from_lark",
        "hypothesis.extra.numpy.arrays",
        "hypothesis.extra.numpy.array_shapes",
        "hypothesis.extra.numpy.unsigned_integer_dtypes",
        "hypothesis.extra.numpy.integer_dtypes",
        "hypothesis.extra.numpy.floating_dtypes",
        "hypothesis.extra.numpy.complex_number_dtypes",
        "hypothesis.extra.numpy.datetime64_dtypes",
        "hypothesis.extra.numpy.timedelta64_dtypes",
        "hypothesis.extra.numpy.byte_string_dtypes",
        "hypothesis.extra.numpy.unicode_string_dtypes",
        "hypothesis.extra.numpy.array_dtypes",
        "hypothesis.extra.numpy.nested_dtypes",
        "hypothesis.extra.numpy.valid_tuple_axes",
        "hypothesis.extra.numpy.broadcastable_shapes",
        "hypothesis.extra.pandas.indexes",
        "hypothesis.extra.pandas.series",
        "hypothesis.extra.pandas.columns",
        "hypothesis.extra.pandas.data_frames",
        "hypothesis.provisional.domains",
        "hypothesis.stateful.run_state_machine_as_test",
        "hypothesis.stateful.rule",
        "hypothesis.stateful.initialize",
        "hypothesis.strategies.floats",
        "hypothesis.strategies.lists",
        "hypothesis.strategies.sets",
        "hypothesis.strategies.frozensets",
        "hypothesis.strategies.iterables",
        "hypothesis.strategies.dictionaries",
        "hypothesis.strategies.characters",
        "hypothesis.strategies.text",
        "hypothesis.strategies.from_regex",
        "hypothesis.strategies.binary",
        "hypothesis.strategies.fractions",
        "hypothesis.strategies.decimals",
        "hypothesis.strategies.recursive",
        "hypothesis.strategies.complex_numbers",
        "hypothesis.strategies.shared",
        "hypothesis.strategies.uuids",
        "hypothesis.strategies.runner",
        "hypothesis.strategies.functions",
        "hypothesis.strategies.datetimes",
        "hypothesis.strategies.times",
    )

    def leave_Call(self, original_node, updated_node):
        """Convert positional to keyword arguments."""
        metadata = self.get_metadata(cst.metadata.QualifiedNameProvider, original_node)
        qualnames = {qn.name for qn in metadata}

        # If this isn't one of our known functions, or it has no posargs, stop there.
        if (
            len(qualnames) != 1
            or not qualnames.intersection(self.kwonly_functions)
            or not m.matches(
                updated_node,
                m.Call(
                    func=m.DoesNotMatch(m.Call()),
                    args=[m.Arg(keyword=None), m.ZeroOrMore()],
                ),
            )
        ):
            return updated_node

        # Get the actual function object so that we can inspect the signature.
        # This does e.g. incur a dependency on Numpy to fix Numpy-dependent code,
        # but having a single source of truth about the signatures is worth it.
        params = signature(get_fn(*qualnames)).parameters.values()

        # st.floats() has a new allow_subnormal kwonly argument not at the end,
        # so we do a bit more of a dance here.
        if qualnames == {"hypothesis.strategies.floats"}:
            params = [p for p in params if p.name != "allow_subnormal"]

        if len(updated_node.args) > len(params):
            return updated_node

        # Create new arg nodes with the newly required keywords
        assign_nospace = cst.AssignEqual(
            whitespace_before=cst.SimpleWhitespace(""),
            whitespace_after=cst.SimpleWhitespace(""),
        )
        newargs = [
            arg
            if arg.keyword or arg.star or p.kind is not Parameter.KEYWORD_ONLY
            else arg.with_changes(keyword=cst.Name(p.name), equal=assign_nospace)
            for p, arg in zip(params, updated_node.args)
        ]
        return updated_node.with_changes(args=newargs)
