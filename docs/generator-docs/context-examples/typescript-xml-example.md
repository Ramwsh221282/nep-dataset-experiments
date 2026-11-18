# TypeScript с JSX (TSX)

## Исходный код

```tsx
export interface UserCardProps {
  user: User;
  compact?: boolean;
  onSelect: (id: string) => void;
}

export const UserCard = ({
  user,
  compact = false,
  onSelect,
}: UserCardProps): React.ReactElement => {
  const title = formatUserName(user);
  let subtitle: string = compact ? "" : user.email;

  function selectUser(id: string): void {
    onSelect(id);
  }

  const handleClick = (): void => {
    selectUser(user.id);
  };

  let formatLabel = (value: string): string => value.trim();

  return (
    <button onClick={handleClick} aria-label={formatLabel(title)}>
      <strong>{title}</strong>
      {!compact && <span>{subtitle}</span>}
    </button>
  );
};
```

## Обработанный контекст

```text
export interface UserCardProps {
  user: User;
  compact?: boolean;
  onSelect: (id: string) => void;
}

export const UserCard = ({
  user,
  compact = false,
  onSelect,
}: UserCardProps): React.ReactElement => {
  const title = formatUserName(user);
  let subtitle: string = compact ? "" : user.email;

  function selectUser(id: string): void;
  const handleClick = (): void => {/* тело функции опущено */};
  let formatLabel = (value: string): string => {/* тело функции опущено */};

  return (
    <button onClick={handleClick} aria-label={formatLabel(title)}>
      <strong>{title}</strong>
      {!compact && <span>{subtitle}</span>}
    </button>
  );
};
```
