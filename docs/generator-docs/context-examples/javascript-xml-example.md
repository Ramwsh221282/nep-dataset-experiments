# JavaScript с JSX

## Исходный код

```jsx
export function SearchPanel({ initialQuery = "", onSearch }) {
  const placeholder = "Search users";
  let query = initialQuery;

  function normalizeQuery(value) {
    return value.trim().toLowerCase();
  }

  const handleChange = (event) => {
    query = event.target.value;
  };

  let submitSearch = () => {
    onSearch(normalizeQuery(query));
  };

  return (
    <form onSubmit={(event) => {
      event.preventDefault();
      submitSearch();
    }}>
      <input
        name="query"
        defaultValue={initialQuery}
        placeholder={placeholder}
        onChange={handleChange}
      />
      <button type="submit">Search</button>
    </form>
  );
}
```

## Обработанный контекст

```text
export function SearchPanel({ initialQuery = "", onSearch }) {
  const placeholder = "Search users";
  let query = initialQuery;

  function normalizeQuery(value);
  const handleChange = (event) => {/* тело функции опущено */};
  let submitSearch = () => {/* тело функции опущено */};

  return (
    <form onSubmit={(event) => {/* тело функции опущено */}}>
      <input
        name="query"
        defaultValue={initialQuery}
        placeholder={placeholder}
        onChange={handleChange}
      />
      <button type="submit">Search</button>
    </form>
  );
}
```
