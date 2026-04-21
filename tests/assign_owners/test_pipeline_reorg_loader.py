import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.ci.assign_owners.__main__ import load_pipeline_reorg_owners


class LoadPipelineReorgOwnersTests(unittest.TestCase):
    def _write(self, root: Path, name: str, body: str) -> None:
        (root / name).write_text(textwrap.dedent(body))

    def test_parses_name_owner_pairs_with_real_name_comment(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "a.yaml", """
                - name: Alpha Job
                  owner_id: U111 # Alice A.
                - name: Bravo Job
                  owner_id: U222 # Bob B.
            """)
            entries = load_pipeline_reorg_owners(root)
        self.assertEqual(entries, {
            "Alpha Job": {"name": "Alpha Job", "id": "U111", "owner_name": "Alice A."},
            "Bravo Job": {"name": "Bravo Job", "id": "U222", "owner_name": "Bob B."},
        })

    def test_handles_missing_owner_comment_and_quoted_names(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "c.yaml", """
                - name: "Quoted Name"
                  owner_id: U333
                - name: 'Single Quoted'
                  owner_id:   U444   # Spaced Owner
            """)
            entries = load_pipeline_reorg_owners(root)
        self.assertEqual(entries, {
            "Quoted Name": {"name": "Quoted Name", "id": "U333", "owner_name": ""},
            "Single Quoted": {"name": "Single Quoted", "id": "U444", "owner_name": "Spaced Owner"},
        })

    def test_returns_empty_when_dir_missing(self) -> None:
        self.assertEqual(load_pipeline_reorg_owners(Path("/nonexistent/path/xyz")), {})

    def test_ignores_orphan_owner_id_without_preceding_name(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "x.yaml", """
                  owner_id: U999 # Orphan
                - name: Real Job
                  owner_id: U777 # Real Owner
            """)
            entries = load_pipeline_reorg_owners(root)
        self.assertEqual(entries, {"Real Job": {"name": "Real Job", "id": "U777", "owner_name": "Real Owner"}})


if __name__ == "__main__":
    unittest.main()
