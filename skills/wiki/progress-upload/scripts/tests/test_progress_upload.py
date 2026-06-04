"""derive_op / derive_run_id / short_display_path 的最小验证（纯 stdlib unittest）。

运行：
    cd skills/wiki/progress-upload/scripts && python -m unittest discover -s tests -p 'test_*.py'
"""
import datetime
import sys
import unittest
from pathlib import Path

# 让 `import progress_upload` 能在 tests/ 子目录下找到上一级的模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from progress_upload import (  # noqa: E402
    derive_op,
    derive_run_id,
    resolve_upload_date,
    short_display_path,
)


class DeriveOpTest(unittest.TestCase):
    def test_strips_progress_md_suffix(self):
        self.assertEqual(
            derive_op("/x/claude/run0/gemm_add_relu.progress.md"), "gemm_add_relu")

    def test_strips_plain_md_suffix(self):
        self.assertEqual(derive_op("/x/add.md"), "add")

    def test_explicit_op_wins(self):
        self.assertEqual(
            derive_op("/x/gemm_add_relu.progress.md", "custom_op"), "custom_op")

    def test_no_known_suffix_returns_basename(self):
        self.assertEqual(derive_op("/x/notes"), "notes")


class DeriveRunIdTest(unittest.TestCase):
    def test_experiment_name_is_filename(self):
        # 文件名 = 实验名 (run 目录上两级), 与 run 号无关
        self.assertEqual(
            derive_run_id("/o/debug_test_v4/claude/run0/mla_prolog.progress.md"),
            "debug_test_v4")
        self.assertEqual(
            derive_run_id("/o/parallel_test/openai/run3/gather.progress.md"),
            "parallel_test")

    def test_same_experiment_diff_run_overwrites(self):
        # 不带 run 号: 同实验不同 run → 同一文件名 (覆盖, 符合"不需要 run_id")
        a = derive_run_id("/o/exp/claude/run0/op.progress.md")
        b = derive_run_id("/o/exp/claude/run1/op.progress.md")
        self.assertEqual(a, b)

    def test_no_experiment_dir_returns_none(self):
        self.assertIsNone(derive_run_id("/x/output/op.progress.md"))

    def test_explicit_run_wins(self):
        self.assertEqual(
            derive_run_id("/o/exp/claude/run0/op.progress.md", "retry"), "retry")


class ShortDisplayPathTest(unittest.TestCase):
    def test_absolute_path_reduced_to_two_segments(self):
        self.assertEqual(
            short_display_path("/data2/w/raw/sessions/progress/gemm_add_relu/run0.progress.md"),
            "gemm_add_relu/run0.progress.md",
        )

    def test_bare_filename_unchanged(self):
        self.assertEqual(short_display_path("run0.progress.md"), "run0.progress.md")

    def test_empty_path(self):
        self.assertEqual(short_display_path(""), "")


class ResolveUploadDateTest(unittest.TestCase):
    def test_valid_date_kept(self):
        self.assertEqual(resolve_upload_date("2026-06-04"), "2026-06-04")

    def test_none_defaults_to_today(self):
        self.assertEqual(resolve_upload_date(None), datetime.date.today().isoformat())

    def test_invalid_date_defaults_to_today(self):
        today = datetime.date.today().isoformat()
        for bad in ("", "2026/06/04", "20260604", "../../etc"):
            self.assertEqual(resolve_upload_date(bad), today)


if __name__ == "__main__":
    unittest.main()
