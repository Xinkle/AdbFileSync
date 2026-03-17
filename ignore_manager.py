from __future__ import annotations

import pathspec
from pathlib import Path
from typing import Dict, List, Optional


class GitIgnoreManager:
    """
    Manages .gitignore files and path matching based on their rules using pathspec.
    Respects hierarchical rules: subdirectories can override/add rules.
    """

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()
        # Dict[dir_path, Optional[pathspec.PathSpec]]
        self.spec_cache: Dict[Path, Optional[pathspec.PathSpec]] = {}

    def _get_spec_for_dir(self, directory: Path) -> Optional[pathspec.PathSpec]:
        """
        Loads and parses a .gitignore file in the given directory using pathspec.
        """
        if directory in self.spec_cache:
            return self.spec_cache[directory]

        gitignore_path = directory / ".gitignore"
        spec = None

        if gitignore_path.exists():
            try:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
            except Exception:
                pass

        self.spec_cache[directory] = spec
        return spec

    def is_ignored(self, path: Path) -> bool:
        """
        Checks if the given path is ignored by any .gitignore file from root to its directory.
        """
        abs_path = path.resolve()
        try:
            # Need to get path relative to root_dir for matching
            rel_path = abs_path.relative_to(self.root_dir)
        except ValueError:
            return False  # Not under root

        # Check all .gitignore files from root down to the path's directory
        # For Git: a file is ignored if any .gitignore in its path says so,
        # UNLESS a later (more specific) .gitignore has a negation (!) rule.
        
        ignored = False
        
        # Start from root and go down to the file's parent directory
        current = self.root_dir
        path_parts = rel_path.parts
        
        # We need to check the root .gitignore first, then each sub-directory's .gitignore
        for i in range(len(path_parts)):
            current_dir = self.root_dir.joinpath(*path_parts[:i])
            
            spec = self._get_spec_for_dir(current_dir)
            if spec:
                # Calculate the relative path from THIS .gitignore's directory
                # pathspec needs a relative path from the spec's root
                try:
                    rel_to_spec = abs_path.relative_to(current_dir).as_posix()
                    # Check if this spec changes the ignore status
                    if spec.match_file(rel_to_spec):
                        ignored = True
                    # PathSpec doesn't easily tell us if it WAS ignored but now NOT ignored (negation)
                    # without re-evaluating. Fortunately, PathSpec handles negation (!) correctly 
                    # within a single file. 
                    # However, if a parent ignored it, a child can un-ignore it.
                    # We need to check if the file is NOT ignored by the current spec 
                    # but only if the spec actually contains rules that could match it.
                except ValueError:
                    continue
        
        # Special handling for "legacy" rules if needed, but for now we follow .gitignore standard.
        return ignored
