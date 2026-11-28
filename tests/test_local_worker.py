import subprocess
from threading import Event
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import os
import click
from flask.cli import run_command

from app.local_worker import LocalWorker, should_start, init_flask_cli_worker


class LocalWorkerTests(unittest.TestCase):
    def cli_app(self, testing=False):
        return SimpleNamespace(testing=testing,config={'COMMERCE_HUB_ENABLED':True},extensions={})

    @patch.dict(os.environ,{'FLASK_RUN_FROM_CLI':'true','COMMERCE_WORKER_DATABASE_URL':'configured','WERKZEUG_RUN_MAIN':'false'})
    @patch('app.local_worker.LocalWorker')
    def test_flask_run_starts_once_and_stops_when_command_closes(self, supervisor):
        app=self.cli_app()
        with click.Context(run_command):
            init_flask_cli_worker(app)
            init_flask_cli_worker(app)
            supervisor.assert_called_once()
            supervisor.return_value.__enter__.assert_called_once()
            supervisor.return_value.__exit__.assert_not_called()
        supervisor.return_value.__exit__.assert_called_once()

    @patch.dict(os.environ,{'FLASK_RUN_FROM_CLI':'true','COMMERCE_WORKER_DATABASE_URL':'configured','WERKZEUG_RUN_MAIN':'false'})
    @patch('app.local_worker.LocalWorker')
    def test_cli_tests_and_non_run_commands_never_start_worker(self, supervisor):
        init_flask_cli_worker(self.cli_app())
        with click.Context(click.Command('routes')):
            init_flask_cli_worker(self.cli_app())
        with click.Context(run_command):
            init_flask_cli_worker(self.cli_app(testing=True))
        with patch.dict(os.environ,{'WERKZEUG_RUN_MAIN':'true'}),click.Context(run_command):
            init_flask_cli_worker(self.cli_app())
        supervisor.assert_not_called()

    @patch.dict(os.environ,{'FLASK_RUN_FROM_CLI':'false','COMMERCE_WORKER_DATABASE_URL':'configured'})
    @patch('app.local_worker.LocalWorker')
    def test_importing_for_wsgi_or_scripts_never_starts_cli_worker(self, supervisor):
        with click.Context(run_command):
            init_flask_cli_worker(self.cli_app())
        supervisor.assert_not_called()

    def test_only_reloader_parent_with_enabled_config_owns_worker(self):
        settings = {'COMMERCE_HUB_ENABLED': True}
        env = {'COMMERCE_WORKER_DATABASE_URL': 'configured'}
        self.assertTrue(should_start(settings, env))
        self.assertFalse(should_start(settings, {**env, 'WERKZEUG_RUN_MAIN': 'true'}))
        self.assertFalse(should_start({'COMMERCE_HUB_ENABLED': False}, env))
        self.assertFalse(should_start(settings, {}))

    @patch('app.local_worker.subprocess.Popen')
    def test_context_stops_its_worker_and_uses_secret_free_arguments(self, spawn):
        started = Event()
        process = Mock()
        process.poll.return_value = None
        spawn.side_effect = lambda *args, **kwargs: (started.set() or process)
        with LocalWorker(poll_seconds=.005):
            self.assertTrue(started.wait(2))
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=3)
        command = spawn.call_args.args[0]
        self.assertEqual(command[1:], ['-B', '-m', 'commerce_hub.worker'])
        self.assertNotIn('env', spawn.call_args.kwargs)

    @patch('app.local_worker.subprocess.Popen')
    def test_worker_restarts_after_exit(self, spawn):
        restarted = Event()
        first, second = Mock(), Mock()
        first.poll.return_value = 1
        second.poll.return_value = None
        def create(*_args, **_kwargs):
            if not spawn.call_count > 1:
                return first
            restarted.set()
            return second
        spawn.side_effect = create
        with LocalWorker(retry_seconds=.005, poll_seconds=.005):
            self.assertTrue(restarted.wait(2))
        second.terminate.assert_called_once()
        self.assertEqual(spawn.call_count, 2)

    @patch('app.local_worker.subprocess.Popen')
    def test_unresponsive_child_is_killed_on_shutdown(self, spawn):
        started = Event()
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('worker', 3), 0]
        spawn.side_effect = lambda *args, **kwargs: (started.set() or process)
        with LocalWorker(poll_seconds=.005):
            self.assertTrue(started.wait(2))
        process.kill.assert_called_once()

    @patch('app.local_worker.LocalWorker.source_stamp')
    @patch('app.local_worker.subprocess.Popen')
    def test_code_change_restarts_worker_to_load_new_connector_imports(self, spawn, stamp):
        restarted=Event()
        first,second=Mock(),Mock()
        first.poll.return_value=second.poll.return_value=None
        stamp.side_effect=lambda: ('before',) if not spawn.called else ('after',)
        def create(*_args,**_kwargs):
            if spawn.call_count==1:
                return first
            restarted.set()
            return second
        spawn.side_effect=create
        with LocalWorker(poll_seconds=.005):
            self.assertTrue(restarted.wait(2))
        first.terminate.assert_called_once()
        second.terminate.assert_called_once()
        self.assertEqual(spawn.call_count,2)
