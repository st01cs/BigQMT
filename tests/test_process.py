import unittest

from bigqmt.core.qmt._process import (
    any_process_running,
    find_process_pid,
    parse_tasklist_csv,
    running_processes,
)


SAMPLE_TASKLIST = (
    '"XtItClient.exe","1234","Console","1","45,678 K"\n'
    '"svchost.exe","2345","Services","0","8,900 K"\n'
    '"python.exe","3456","Console","2","12,345 K"\n'
)


class ParseTasklistTest(unittest.TestCase):
    def test_parses_image_and_pid(self):
        procs = parse_tasklist_csv(SAMPLE_TASKLIST)
        self.assertEqual(procs["xtitclient.exe"], 1234)
        self.assertEqual(procs["svchost.exe"], 2345)
        self.assertEqual(procs["python.exe"], 3456)

    def test_case_insensitive_keys(self):
        procs = parse_tasklist_csv(SAMPLE_TASKLIST)
        self.assertIn("XTITCLIENT.EXE", {k.upper() for k in procs})

    def test_empty_input(self):
        self.assertEqual(parse_tasklist_csv(""), {})
        self.assertEqual(parse_tasklist_csv("   \n  "), {})

    def test_malformed_rows_ignored(self):
        text = '"only_name"\n"a","not_a_pid"\n"b","77"\n'
        procs = parse_tasklist_csv(text)
        self.assertEqual(procs, {"b": 77})


class RunningProcessesTest(unittest.TestCase):
    def test_injected_reader(self):
        def reader():
            return SAMPLE_TASKLIST

        procs = running_processes(reader=reader)
        self.assertEqual(procs["xtitclient.exe"], 1234)


class FindProcessPidTest(unittest.TestCase):
    def test_finds_matching_pid(self):
        self.assertEqual(
            find_process_pid("XtItClient.exe", reader=lambda: SAMPLE_TASKLIST),
            1234,
        )

    def test_not_running_returns_none(self):
        self.assertIsNone(
            find_process_pid("not_running.exe", reader=lambda: SAMPLE_TASKLIST)
        )


class AnyProcessRunningTest(unittest.TestCase):
    def test_true_when_any_matches(self):
        self.assertTrue(
            any_process_running(
                ["python.exe", "ghost.exe"],
                reader=lambda: SAMPLE_TASKLIST,
            )
        )

    def test_false_when_none_match(self):
        self.assertFalse(
            any_process_running(
                ["ghost1.exe", "ghost2.exe"],
                reader=lambda: SAMPLE_TASKLIST,
            )
        )


if __name__ == "__main__":
    unittest.main()
