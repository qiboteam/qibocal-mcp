"""Incremental, standard Qibocal output for one calibration session."""

import json
from dataclasses import replace
from pathlib import Path

from qibo.backends import construct_backend
from qibocal.auto.history import History
from qibocal.auto.operation import DEFAULT_PARENT_PARAMETERS, Protocol
from qibocal.auto.output import Metadata, Output
from qibocal.auto.task import Action, Completed, Task, TaskId, Targets
from qibocal.calibration import CalibrationPlatform


class SessionReport:
    """Adapt direct executions to Qibocal's task-backed output format."""

    def __init__(
        self,
        path: Path,
        platform_name: str,
        platform: CalibrationPlatform,
        protocols: dict[str, Protocol],
    ):
        self.path = path
        history = History()
        if (path / "meta.json").exists():
            meta = Metadata.load(path)
            if meta.platform != platform_name:
                raise ValueError("Report belongs to a different platform")
            for name in json.loads((path / "history.json").read_text(encoding="utf-8")):
                folder = path / "data" / name
                action = Action.load(folder)
                stats = meta.stats[name]
                completed = Completed(
                    task=Task(action, protocols[action.operation]),
                    path=folder,
                    data_time=stats["acquisition"],
                    results_time=stats["fit"],
                )
                task_id = history.push(completed)
                if str(task_id) != name:
                    raise ValueError(f"Inconsistent report history entry: {name}")
        else:
            meta = Metadata.generate(
                construct_backend(backend="qibolab", platform=platform)
            )
            meta.platform = platform_name
            meta.targets = []
            meta.start()
            path.mkdir(parents=True)
            Output(history, meta, platform).dump(path)
            Output.update_platform(platform, path)
        self.output = Output(history, meta)

    def add(
        self, completed: Completed, protocol: str, parameters: dict, targets: Targets
    ) -> Completed:
        """Persist a task, preserving resolved acquisition parameters and timing."""
        if completed.bound is None:
            raise ValueError("Completed execution has no bound protocol")
        bound = completed.bound
        parameters = dict(parameters)
        parameters.update({
            name: getattr(bound.parameters, name)
            for name in DEFAULT_PARENT_PARAMETERS
            if hasattr(bound.parameters, name)
        })
        action = Action(
            id=protocol,
            operation=protocol,
            targets=targets,
            parameters=parameters,
            update=False,
        )
        history = self.output.history
        task_id = TaskId(protocol, len(history[protocol]))
        folder = history.task_path(task_id, self.path)
        completed = replace(
            completed, task=Task(action, bound.protocol), path=folder
        )
        completed.dump_parameters()
        completed.dump_data()
        completed.dump_results()
        history.push(completed)
        for target in action.targets:
            for qubit in target if isinstance(target, (list, tuple)) else [target]:
                if qubit not in self.output.meta.targets:
                    self.output.meta.targets.append(qubit)
        return completed

    def save(self, platform: CalibrationPlatform) -> None:
        """Refresh metadata, execution order and the session's current platform."""
        self.output.meta.end()
        self.output.platform = platform
        self.output.dump(self.path)

    def find_run(self, run_id: str) -> Completed:
        """Restore a run from this report only, not from arbitrary output paths."""
        for completed in self.output.history.values():
            metadata = json.loads(
                (completed.path / "run.json").read_text(encoding="utf-8")
            )
            if metadata["run_id"] == run_id:
                return completed
        raise ValueError(f"Unknown run_id in this server session: {run_id}")
