# `streamable`: Stream-like manipulation of iterables

# What my project does
A `Stream[T]` decorates an `Iterable[T]` with a **fluent interface** enabling the **chaining of lazy operations**:
- **mapping** (concurrently)
- **flattening** (concurrently)
- **grouping** by key, by batch size, by time interval
- **filtering**
- **truncating**
- **catching** exceptions
- **throttling** the rate of iterations
- **observing** the progress of iterations

For more details and examples, check the [Operations section in the README](https://github.com/ebonnal/streamable?tab=readme-ov-file#-operations)


|||
|--|--|
|🔗 *Fluent*|chain methods!|
|🇹 *Typed*|**type-annotated** and [`mypy`](https://github.com/python/mypy)able|
|💤 *Lazy*|operations are **lazily evaluated** at iteration time|
|🔄 *Concurrent*|**thread**-based or `asyncio`-based concurrency|
|🛡️ *Robust*|unit-tested for **Python 3.7 to 3.12** with 100% coverage|
|🪶 *Minimalist*|`pip install streamable` with **no additional dependencies**|

---


# 1. install

```bash
pip install streamable
```

# 2. import
```python
from streamable import Stream
```

# 3. init
Instantiate a `Stream[T]` from an `Iterable[T]`.

```python
integers: Stream[int] = Stream(range(10))
```

# 4. operate
- `Stream`s are ***immutable***: applying an operation returns a new stream.

- Operations are ***lazy***: only evaluated at iteration time. See the [Operations section in the README](https://github.com/ebonnal/streamable?tab=readme-ov-file#-operations).

```python
inverses: Stream[float] = (
    integers
    .map(lambda n: round(1 / n, 2))
    .catch(ZeroDivisionError)
)
```

# 5. iterate
- Iterate over a `Stream[T]` as you would over any other `Iterable[T]`.
- Source elements are ***processed on-the-fly***.

- collect it:
```python
>>> list(inverses)
[1.0, 0.5, 0.33, 0.25, 0.2, 0.17, 0.14, 0.12, 0.11]
>>> set(inverses)
{0.5, 1.0, 0.2, 0.33, 0.25, 0.17, 0.14, 0.12, 0.11}
```

- reduce it:
```python
>>> sum(integers)
2.82
>>> max(inverses)
1.0
>>> from functools import reduce
>>> reduce(..., inverses)
```

- loop it:
```python
>>> for inverse in inverses:
>>>    ...
```

- next it:
```python
>>> inverses_iter = iter(inverses)
>>> next(inverses_iter)
1.0
>>> next(inverses_iter)
0.5
```

# Target Audience
As a Data Engineer in a startup I found it especially useful when I had to develop Extract-Transform-Load custom scripts in an easy-to-read way.

Here is a toy example (that you can copy-paste and run) that creates a CSV file containing all 67 quadrupeds from the 1st, 2nd, and 3rd generations of Pokémons (kudos to [PokéAPI](https://pokeapi.co/)):
```python
import csv
from datetime import timedelta
import itertools
import requests
from streamable import Stream

with open("./quadruped_pokemons.csv", mode="w") as file:
    fields = ["id", "name", "is_legendary", "base_happiness", "capture_rate"]
    writer = csv.DictWriter(file, fields, extrasaction='ignore')
    writer.writeheader()
    (
        # Infinite Stream[int] of Pokemon ids starting from Pokémon #1: Bulbasaur
        Stream(itertools.count(1))
        # Limits to 16 requests per second to be friendly to our fellow PokéAPI devs
        .throttle(per_second=16)
        # GETs pokemons concurrently using a pool of 8 threads
        .map(lambda poke_id: f"https://pokeapi.co/api/v2/pokemon-species/{poke_id}")
        .map(requests.get, concurrency=8)
        .foreach(requests.Response.raise_for_status)
        .map(requests.Response.json)
        # Stops the iteration when reaching the 1st pokemon of the 4th generation
        .truncate(when=lambda poke: poke["generation"]["name"] == "generation-iv")
        .observe("pokemons")
        # Keeps only quadruped Pokemons
        .filter(lambda poke: poke["shape"]["name"] == "quadruped")
        .observe("quadruped pokemons")
        # Catches errors due to None "generation" or "shape"
        .catch(
            TypeError,
            when=lambda error: str(error) == "'NoneType' object is not subscriptable"
        )
        # Writes a batch of pokemons every 5 seconds to the CSV file
        .group(interval=timedelta(seconds=5))
        .foreach(writer.writerows)
        .flatten()
        .observe("written pokemons")
        # Catches exceptions and raises the 1st one at the end of the iteration
        .catch(finally_raise=True)
        # Actually triggers an iteration (the lines above define lazy operations)
        .count()
    )
```

# Comparison
A lot of other libraries have filled this desire to chain lazy operations over an iterable and this feels indeed like *"Yet Another Stream-like Lib"* (e.g. see [this stackoverflow question](https://stackoverflow.com/questions/24831476/what-is-the-python-way-of-chaining-maps-and-filters/77978940?noredirect=1#comment138494051_77978940)).

The most supported of them is probably [PyFunctional](https://github.com/EntilZha/PyFunctional), but for my use case I couldn't use it out-of-the-box, due to the lack of:
- generic typing (`class Stream[T](Iterable[T]))`)
- throttling of iteration's rate (`.throttle`)
- logging of iteration's process (`.observe`)
- catching of exceptions (`.catch`)

I could have worked on pull requests implementing these points into PyFunctional but I have rather started from scratch in order to take my shot at:
- Proposing another fluent interface (namings and signatures).
- Leveraging a visitor pattern to decouple the declaration of a `Stream[T]` from the construction of an `Iterator[T]` (at iteration time i.e. in the `__iter__` method).
- Proposing a minimalist design: a `Stream[T]` is just an `Iterable[T]` decorated with chainable lazy operations and it is not responsible for the opinionated logic of creating its data source and consuming its elements:
  - let's use the `reduce` function from `functools` instead of relying on a `stream.reduce` method
  - let's use `parquet.ParquetFile.iter_batches` from `pyarrow` instead of relying on a `stream.from_parquet` method
  - let's use `bigquery.Client.insert_rows_json` from `google.cloud` instead of relying on a `stream.to_bigquery` method
  - same for `json`, `csv`, `psycopg`, `stripe`, ... let's use our favorite specialized libraries

Thank you for your time,
