# ignore_manager.py
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import List, Dict, Tuple


class GitIgnoreManager:
    """
    Manages .gitignore files and path matching based on their rules.
    Respects hierarchical rules: subdirectories can override/add rules.
    """

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()
        # Dict[dir_path, List[Tuple[pattern, is_negation]]]
        self.rules_cache: Dict[Path, List[Tuple[str, bool]]] = {}
        # Dict[dir_path, bool] - cache whether a directory itself is ignored
        self.dir_ignore_cache: Dict[Path, bool] = {}

    def _load_gitignore(self, directory: Path) -> List[Tuple[str, bool]]:
        """
        Loads and parses a .gitignore file in the given directory.
        """
        if directory in self.rules_cache:
            return self.rules_cache[directory]

        rules: List[Tuple[str, bool]] = []
        gitignore_path = directory / ".gitignore"

        if gitignore_path.exists():
            try:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue

                        is_negation = False
                        if line.startswith("!"):
                            is_negation = True
                            line = line[1:]

                        rules.append((line, is_negation))
            except Exception:
                pass

        self.rules_cache[directory] = rules
        return rules

    def is_ignored(self, path: Path) -> bool:
        """
        Checks if the given path is ignored by any .gitignore file from root to its directory.
        """
        abs_path = path.resolve()
        try:
            rel_path = abs_path.relative_to(self.root_dir)
        except ValueError:
            return False  # Not under root

        # Check if any parent directory is already ignored
        for parent in list(abs_path.parents)[::-1]: # From root-most down
            if parent == self.root_dir or self.root_dir in parent.parents:
                if parent in self.dir_ignore_cache and self.dir_ignore_cache[parent]:
                    return True

        # If not already determined by parent cache, check rules
        current = self.root_dir
        path_parts = rel_path.parts
        
        ignored = False
        
        # Check root first, then each subdirectory part
        for i in range(len(path_parts) + 1):
            current_dir = self.root_dir.joinpath(*path_parts[:i])
            
            # If we're at a directory and it's already in cache, use it
            if current_dir.is_dir() and current_dir in self.dir_ignore_cache:
                ignored = self.dir_ignore_cache[current_dir]
                if ignored: break
                continue

            rules = self._load_gitignore(current_dir if current_dir.is_dir() else current_dir.parent)
            
            for pattern, is_negation in rules:
                # 1. Match the file/dir name itself
                if fnmatch.fnmatch(abs_path.name, pattern):
                    ignored = not is_negation
                # 2. Match the relative path from this gitignore's directory
                try:
                    # current_dir might be the file itself in the last iteration
                    rule_root = current_dir if current_dir.is_dir() else current_dir.parent
                    rel_to_this_git = abs_path.relative_to(rule_root).as_posix()
                    if fnmatch.fnmatch(rel_to_this_git, pattern) or \
                       fnmatch.fnmatch(rel_to_this_git, pattern.rstrip("/") + "/*"):
                        ignored = not is_negation
                except ValueError:
                    continue
            
            # Cache directory status
            if current_dir.is_dir():
                self.dir_ignore_cache[current_dir] = ignored
            
            if ignored:
                break

        return ignored
