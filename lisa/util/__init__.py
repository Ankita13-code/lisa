# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import concurrent.futures
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Pattern, Type, TypeVar

import pluggy

T = TypeVar("T")

# hooks manager helper, they must be same name.
_NAME_LISA = "lisa"
plugin_manager = pluggy.PluginManager(_NAME_LISA)
hookspec = pluggy.HookspecMarker(_NAME_LISA)
hookimpl = pluggy.HookimplMarker(_NAME_LISA)


class LisaException(Exception):
    ...


class SkippedException(Exception):
    """
    A test case can be skipped based on runtime information.
    """

    ...


class NotRunException(Exception):
    """
    If current environment doesn't meet requirement of a test case, it can be set to
    not run and try next environment.
    """

    ...


class PassedException(Exception):
    """
    A test case may verify several things, but part of verification cannot be done. In
    this situation, the test case may be considered to passed also. Raise this
    Exception to bring an error message, and make test pass also.
    """

    ...


class ContextMixin:
    def get_context(self, context_type: Type[T]) -> T:
        if not hasattr(self, "_context"):
            self._context: T = context_type()
        else:
            assert isinstance(
                self._context, context_type
            ), f"actual: {type(self._context)}"
        return self._context


class InitializableMixin:
    """
    This mixin uses to do one time but delay initialization work.

    __init__ shouldn't do time costing work as most design recommendation. But
    something may be done let an object works. _initialize uses to call for one time
    initialization. If an object is initialized, it do nothing.
    """

    def __init__(self) -> None:
        super().__init__()
        self._is_initialized: bool = False

    def _initialize(self, *args: Any, **kwargs: Any) -> None:
        """
        override for initialization logic. This mixin makes sure it's called only once.
        """
        raise NotImplementedError()

    def initialize(self, *args: Any, **kwargs: Any) -> None:
        """
        This is for caller, do not override it.
        """
        if not self._is_initialized:
            try:
                self._is_initialized = True
                self._initialize(*args, **kwargs)
            except Exception as identifier:
                self._is_initialized = False
                raise identifier


class BaseClassMixin:
    @classmethod
    def type_name(cls) -> str:
        raise NotImplementedError()


def get_datetime_path(current: Optional[datetime] = None) -> str:
    if current is None:
        current = datetime.now()
    date = current.utcnow().strftime("%Y%m%d")
    time = current.utcnow().strftime("%H%M%S-%f")[:-3]
    return f"{date}-{time}"


def get_public_key_data(private_key_file_path: str) -> str:

    # TODO: support ppk, if it's needed.
    private_key_path = Path(private_key_file_path)
    if not private_key_path.exists():
        raise LisaException(f"private key file not exist {private_key_file_path}")

    public_key_file = Path(private_key_path).stem
    public_key_path = private_key_path.parent / f"{public_key_file}.pub"
    try:
        with open(public_key_path, "r") as fp:
            public_key_data = fp.read()
    except FileNotFoundError:
        raise LisaException(f"public key file not exist {public_key_path}")
    return public_key_data


def fields_to_dict(src: Any, fields: Iterable[str]) -> Dict[str, Any]:
    """
    copy field values form src to dest, if it's not None
    """
    assert src
    assert fields

    result: Dict[str, Any] = dict()
    for field in fields:
        value = getattr(src, field)
        if value is not None:
            result[field] = value
    return result


def set_filtered_fields(src: Any, dest: Any, fields: List[str]) -> None:
    """
    copy field values form src to dest, if it's not None
    """
    assert src
    assert dest
    assert fields
    for field_name in fields:
        if hasattr(src, field_name):
            field_value = getattr(src, field_name)
        else:
            raise LisaException(f"field {field_name} doesn't exist on src")
        if field_value is not None:
            setattr(dest, field_name, field_value)


def find_patterns_in_lines(lines: str, patterns: List[Pattern[str]]) -> List[List[str]]:
    results: List[List[str]] = [list()] * len(patterns)
    for line in lines.splitlines(keepends=False):
        for index, pattern in enumerate(patterns):
            if not results[index]:
                results[index] = pattern.findall(line)
    return results


def get_matched_str(content: str, pattern: Pattern[str]) -> str:
    result: str = ""
    if content:
        matched_item = pattern.findall(content)
        if matched_item:
            # if something matched, it's like ['matched']
            result = matched_item[0]
    return result


def _cancel_threads(
    futures: List[Any],
    completed_callback: Optional[Callable[[Any], None]] = None,
) -> List[Any]:
    success_futures: List[Any] = []
    for future in futures:
        if future.done() and future.exception():
            # throw exception, if it's not here.
            future.result()
        elif not future.done():
            # cancel running threads. It may need cancellation callback
            result = future.cancel()
            if not result and completed_callback:
                # make sure it's status changed to canceled
                completed_callback(False)
            # join exception back to main thread
            future.result()
        else:
            success_futures.append(future)
    # return empty list to prevent cancel again.
    return success_futures


def run_in_threads(
    methods: List[Any],
    max_workers: int = 0,
    completed_callback: Optional[Callable[[Any], None]] = None,
    # Use Any to prevent cycle import
    log: Optional[Any] = None,
) -> List[Any]:
    """
    start methods in a thread pool
    """

    results: List[Any] = []
    if max_workers <= 0:
        max_workers = len(methods)
    with concurrent.futures.ThreadPoolExecutor(max_workers) as pool:
        futures = [pool.submit(method) for method in methods]
        if completed_callback:
            for future in futures:
                future.add_done_callback(completed_callback)
        try:
            while any(not x.done() for x in futures):
                # if there is any change, skip sleep to get faster
                changed = False
                for future in futures:
                    # join exceptions of subthreads to main thread
                    if future.done():
                        changed = True
                        # removed finished threads
                        futures.remove(future)
                        # exception will throw at this point
                        results.append(future.result())
                        break
                if not changed:
                    time.sleep(0.1)

        except KeyboardInterrupt:
            if log:
                log.info("received CTRL+C, stopping threads...")
            # support to interrupt runs on local debugging.
            futures = _cancel_threads(futures, completed_callback=completed_callback)
            pool.shutdown(True)
        finally:
            if log:
                log.debug("finalizing threads...")
            futures = _cancel_threads(futures, completed_callback=completed_callback)
        for future in futures:
            # exception will throw at this point
            results.append(future.result())
    return results
