import datetime
import logging
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Collection,
    Coroutine,
    Generic,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

from streamable.util import (
    NO_REPLACEMENT,
    get_logger,
    validate_concurrency,
    validate_group_interval,
    validate_group_size,
    validate_throttle_interval,
    validate_throttle_per_second,
    validate_truncate_args,
)

# fmt: off
if TYPE_CHECKING: import builtins
if TYPE_CHECKING: from streamable.visitors import Visitor
# fmt: on

U = TypeVar("U")
T = TypeVar("T")
V = TypeVar("V")


class Stream(Iterable[T]):
    # fmt: off
    @overload
    def __init__(self, source: Iterable[T]) -> None: ...
    @overload
    def __init__(self, source: Callable[[], Iterable[T]]) -> None: ...
    # fmt: on

    def __init__(self, source: Union[Iterable[T], Callable[[], Iterable[T]]]) -> None:
        """
        Initializes a Stream with a source iterable.
        It basically decorates the provided iterable with convenient chainable operation.

        Args:
            source (Union[Iterable[T], Callable[[], Iterable[T]]]): a source iterable or a function returning one (called for each new iteration).
        """
        self._source = source
        self._upstream: "Optional[Stream]" = None

    @property
    def upstream(self) -> "Optional[Stream]":
        """
        Returns:
            Optional[Stream]: Parent stream if any.
        """
        return self._upstream

    @property
    def source(self) -> Union[Iterable, Callable[[], Iterable]]:
        """
        Returns:
            Callable[[], Iterable]: Function called at iteration time (i.e. by `__iter__`) to get a fresh source iterable.
        """
        return self._source

    def __add__(self, other: "Stream[T]") -> "Stream[T]":
        """
        `a + b` returns a stream yielding all elements of `a`, followed by all elements of `b`.
        """
        return cast(Stream[T], Stream([self, other].__iter__).flatten())

    def __iter__(self) -> Iterator[T]:
        from streamable.visitors.iterator import IteratorVisitor

        return self.accept(IteratorVisitor[T]())

    def __repr__(self) -> str:
        from streamable.visitors.representation import RepresentationVisitor

        return self.accept(RepresentationVisitor())

    def accept(self, visitor: "Visitor[V]") -> V:
        """
        Entry point to visit this stream (en.wikipedia.org/wiki/Visitor_pattern).
        """
        return visitor.visit_stream(self)

    def catch(
        self,
        kind: Type[Exception] = Exception,
        when: Callable[[Exception], Any] = bool,
        replacement: T = NO_REPLACEMENT,  # type: ignore
        finally_raise: bool = False,
    ) -> "Stream[T]":
        """
        Catches the upstream exceptions if they are instances of `kind` and they satisfy the `when` predicate.

        Args:
            kind (Type[Exception], optional): The type of exceptions to catch (default is base Exception).
            when (Callable[[Exception], Any], optional): An additional condition that must be satisfied (`when(exception)` must be Truthy) to catch the exception (always satisfied by default).
            replacement (T, optional): The value to yield when an exception is catched (by default nothing will be yielded).
            finally_raise (bool, optional): If True the first catched exception is raised when upstream's iteration ends (default is False).

        Returns:
            Stream[T]: A stream of upstream elements catching the eligible exceptions.
        """
        return CatchStream(self, kind, when, replacement, finally_raise)

    def count(self) -> int:
        """
        Iterates over this stream until exhaustion and returns the count of elements.

        Returns:
            int: Number of elements yielded during an entire iteration over this stream.
        """

        return sum(1 for _ in self)

    def display(self, level: int = logging.INFO) -> "Stream[T]":
        """
        Logs (INFO level) a representation of the stream.

        Args:
            level (int, optional): The level of the log (default is INFO).

        Returns:
            Stream[T]: self.
        """
        get_logger().log(level, repr(self))
        return self

    def filter(self, keep: Callable[[T], Any] = bool) -> "Stream[T]":
        """
        Yields only upstream elements satisfying the `keep` predicate.

        Args:
            keep (Callable[[T], Any], optional): An element will be kept if `keep(elem)` is Truthy (default keeps Truthy elements).

        Returns:
            Stream[T]: A stream of upstream elements satisfying the `keep` predicate.
        """
        return FilterStream(self, keep)

    # fmt: off
    @overload
    def flatten(
        self: "Stream[Iterable[U]]",
        concurrency: int = 1,
    ) -> "Stream[U]": ...

    @overload
    def flatten(
        self: "Stream[Collection[U]]",
        concurrency: int = 1,
    ) -> "Stream[U]": ...

    @overload
    def flatten(
        self: "Stream[Stream[U]]",
        concurrency: int = 1,
    ) -> "Stream[U]": ...

    @overload
    def flatten(
        self: "Stream[Iterator[U]]",
        concurrency: int = 1,
    ) -> "Stream[U]": ...

    @overload
    def flatten(
        self: "Stream[List[U]]",
        concurrency: int = 1,
    ) -> "Stream[U]": ...

    @overload
    def flatten(
        self: "Stream[Sequence[U]]",
        concurrency: int = 1,
    ) -> "Stream[U]": ...

    @overload
    def flatten(
        self: "Stream[builtins.map[U]]",
        concurrency: int = 1,
    ) -> "Stream[U]": ...

    @overload
    def flatten(
        self: "Stream[builtins.filter[U]]",
        concurrency: int = 1,
    ) -> "Stream[U]": ...

    @overload
    def flatten(
        self: "Stream[Set[U]]",
        concurrency: int = 1,
    ) -> "Stream[U]": ...
    # fmt: on

    def flatten(
        self: "Stream[Iterable[U]]",
        concurrency: int = 1,
    ) -> "Stream[U]":
        """
        Iterates over upstream elements, assumed to be iterables, and individually yields the sub-elements.

        Args:
            concurrency (int): The number of threads used to concurrently flatten the upstream iterables (default is 1, meaning no concurrency).
        Returns:
            Stream[R]: A stream of flattened elements from upstream iterables.
        """
        validate_concurrency(concurrency)
        return FlattenStream(self, concurrency)

    def foreach(
        self,
        effect: Callable[[T], Any],
        concurrency: int = 1,
    ) -> "Stream[T]":
        """
        For each upstream element, yields it after having called `effect` on it.
        If `effect(elem)` throws an exception then it will be thrown and `elem` will not be yielded.

        Args:
            effect (Callable[[T], Any]): The function to be applied to each element as a side effect.
            concurrency (int): The number of threads used to concurrently apply the `effect` (default is 1, meaning no concurrency). Preserves the upstream order.
        Returns:
            Stream[T]: A stream of upstream elements, unchanged.
        """
        validate_concurrency(concurrency)
        return ForeachStream(self, effect, concurrency)

    def aforeach(
        self,
        effect: Callable[[T], Coroutine],
        concurrency: int = 1,
    ) -> "Stream[T]":
        """
        For each upstream element, yields it after having called the asynchronous `effect` on it.
        If the `effect(elem)` coroutine throws an exception then it will be thrown and `elem` will not be yielded.

        Args:
            effect (Callable[[T], Any]): The asynchronous function to be applied to each element as a side effect.
            concurrency (int): How many asyncio tasks will run at the same time. Preserves the upstream order.
        Returns:
            Stream[T]: A stream of upstream elements, unchanged.
        """
        validate_concurrency(concurrency)
        return AForeachStream(self, effect, concurrency)

    def group(
        self,
        size: Optional[int] = None,
        interval: Optional[datetime.timedelta] = None,
        by: Optional[Callable[[T], Any]] = None,
    ) -> "Stream[List[T]]":
        """
        Yields upstream elements grouped into lists.
        A group is a list of `size` elements for which `by` returns the same value, but it may contain fewer elements in these cases:
        - `interval` have elapsed since the last yield of a group
        - upstream is exhausted
        - upstream raises an exception

        Args:
            size (Optional[int], optional): Maximum number of elements per group (default is infinity).
            seconds (float, optional): Maximum number of seconds between two yields (default is infinity).
            by (Optional[Callable[[T], Any]], optional): to cogroup elements for which this function returns to the same value. (default does not cogroup).

        Returns:
            Stream[List[T]]: A stream of upstream elements grouped into lists.
        """
        validate_group_size(size)
        validate_group_interval(interval)
        return GroupStream(self, size, interval, by)

    def map(
        self,
        transformation: Callable[[T], U],
        concurrency: int = 1,
    ) -> "Stream[U]":
        """
        Applies `transformation` on upstream elements and yields the results.

        Args:
            transformation (Callable[[T], R]): The function to be applied to each element.
            concurrency (int): The number of threads used to concurrently apply `transformation` (default is 1, meaning no concurrency). Preserves the upstream order.
        Returns:
            Stream[R]: A stream of results of `transformation` applied to upstream elements.
        """
        validate_concurrency(concurrency)
        return MapStream(self, transformation, concurrency)

    def amap(
        self,
        transformation: Callable[[T], Coroutine[Any, Any, U]],
        concurrency: int = 1,
    ) -> "Stream[U]":
        """
        Applies the asynchrounous `transformation` on upstream elements and yields the results in order.

        Args:
            transformation (Callable[[T], Coroutine[Any, Any, U]]): The asynchronous function to be applied to each element.
            concurrency (int): How many asyncio tasks will run at the same time. Preserves the upstream order.
        Returns:
            Stream[R]: A stream of results of `transformation` applied to upstream elements.
        """
        validate_concurrency(concurrency)
        return AMapStream(self, transformation, concurrency)

    def observe(self, what: str = "elements") -> "Stream[T]":
        """
        Logs the progress of the iterations over this stream.

        A logarithmic scale is used to prevent logs flood:
        - a 1st log is produced for the yield of the 1st element
        - a 2nd log is produced when we reach the 2nd element
        - a 3rd log is produced when we reach the 4th element
        - a 4th log is produced when we reach the 8th element
        - ...

        Args:
            what (str): (plural) name representing the objects yielded.

        Returns:
            Stream[T]: A stream of upstream elements whose iteration's progress is logged.
        """
        return ObserveStream(self, what)

    def throttle(
        self,
        per_second: int = cast(int, float("inf")),
        interval: datetime.timedelta = datetime.timedelta(0),
    ) -> "Stream[T]":
        """
        Slows the iteration down to ensure both:
        - a maximum number of yields `per_second`
        - a minimum `interval` between yields`

        Args:
            per_second (float, optional): Maximum number of yields per second (no limit by default).
            interval (datetime.timedelta, optional): Minimum span of time between yields (no limit by default).

        Returns:
            Stream[T]: A stream yielding upstream elements slower, according to `per_second` and `interval` limits.
        """
        validate_throttle_per_second(per_second)
        validate_throttle_interval(interval)
        return ThrottleStream(self, per_second, interval)

    def truncate(
        self, count: Optional[int] = None, when: Optional[Callable[[T], Any]] = None
    ) -> "Stream[T]":
        """
        Stops an iteration as soon as the `when` predicate is satisfied or `count` elements have been yielded.

        Args:
            count (int): The maximum number of elements to yield.
            when (Optional[Callable[[T], Any]], optional): Predicate function whose satisfaction stops an iteration, i.e. only elements for which `when(elem)` is Falsy will be yielded.

        Returns:
            Stream[T]: A stream of at most `count` upstream elements not satisfying the `when` predicate.
        """
        validate_truncate_args(count, when)
        return TruncateStream(self, count, when)


class DownStream(Stream[U], Generic[T, U]):
    """
    Stream that has an upstream.
    """

    def __init__(self, upstream: Stream[T]) -> None:
        Stream.__init__(self, upstream.source)
        self._upstream: Stream[T] = upstream

    @property
    def upstream(self) -> Stream[T]:
        """
        Returns:
            Stream: Parent stream.
        """
        return self._upstream


class CatchStream(DownStream[T, T]):
    def __init__(
        self,
        upstream: Stream[T],
        kind: Type[Exception],
        when: Callable[[Exception], Any],
        replacement: T,
        finally_raise: bool,
    ) -> None:
        super().__init__(upstream)
        self._kind = kind
        self._when = when
        self._replacement = replacement
        self._finally_raise = finally_raise

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_catch_stream(self)


class FilterStream(DownStream[T, T]):
    def __init__(self, upstream: Stream[T], keep: Callable[[T], Any]) -> None:
        super().__init__(upstream)
        self._keep = keep

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_filter_stream(self)


class FlattenStream(DownStream[Iterable[T], T]):
    def __init__(self, upstream: Stream[Iterable[T]], concurrency: int) -> None:
        super().__init__(upstream)
        self._concurrency = concurrency

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_flatten_stream(self)


class ForeachStream(DownStream[T, T]):
    def __init__(
        self, upstream: Stream[T], effect: Callable[[T], Any], concurrency: int
    ) -> None:
        super().__init__(upstream)
        self._effect = effect
        self._concurrency = concurrency

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_foreach_stream(self)


class AForeachStream(DownStream[T, T]):
    def __init__(
        self, upstream: Stream[T], effect: Callable[[T], Coroutine], concurrency: int
    ) -> None:
        super().__init__(upstream)
        self._effect = effect
        self._concurrency = concurrency

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_aforeach_stream(self)


class GroupStream(DownStream[T, List[T]]):
    def __init__(
        self,
        upstream: Stream[T],
        size: Optional[int],
        interval: Optional[datetime.timedelta],
        by: Optional[Callable[[T], Any]],
    ) -> None:
        super().__init__(upstream)
        self._size = size
        self._interval = interval
        self._by = by

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_group_stream(self)


class MapStream(DownStream[T, U]):
    def __init__(
        self, upstream: Stream[T], transformation: Callable[[T], U], concurrency: int
    ) -> None:
        super().__init__(upstream)
        self._transformation = transformation
        self._concurrency = concurrency

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_map_stream(self)


class AMapStream(DownStream[T, U]):
    def __init__(
        self,
        upstream: Stream[T],
        transformation: Callable[[T], Coroutine[Any, Any, U]],
        concurrency: int,
    ) -> None:
        super().__init__(upstream)
        self._transformation = transformation
        self._concurrency = concurrency

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_amap_stream(self)


class ObserveStream(DownStream[T, T]):
    def __init__(self, upstream: Stream[T], what: str) -> None:
        super().__init__(upstream)
        self._what = what

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_observe_stream(self)


class ThrottleStream(DownStream[T, T]):
    def __init__(
        self, upstream: Stream[T], per_second: int, interval: datetime.timedelta
    ) -> None:
        super().__init__(upstream)
        self._per_second = per_second
        self._interval = interval

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_throttle_stream(self)


class TruncateStream(DownStream[T, T]):
    def __init__(
        self,
        upstream: Stream[T],
        count: Optional[int] = None,
        when: Optional[Callable[[T], Any]] = None,
    ) -> None:
        super().__init__(upstream)
        self._count = count
        self._when = when

    def accept(self, visitor: "Visitor[V]") -> V:
        return visitor.visit_truncate_stream(self)
