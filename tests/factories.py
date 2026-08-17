"""Canonical builders for domain types and synthetic JSONL. Keyword-only.

Every builder lives here so that no two test modules can drift apart on defaults.

What is written here is agentlens's belief about the shape of Claude Code's JSONL,
and no test checks that belief against real data, so a wrong default is wrong
everywhere at once. Changes deserve the care of a parser change.
"""
