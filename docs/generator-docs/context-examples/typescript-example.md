# TypeScript без JSX

## Исходный код

```ts
export interface User {
  id: string;
  name: string;
}

export const DEFAULT_LIMIT = 25;
export let resultLimit: number = DEFAULT_LIMIT;

export function findById<T extends { id: string }>(
  items: readonly T[],
  id: string,
): T | undefined {
  return items.find((item) => item.id === id);
}

export class UsersRepository {
  private readonly users: readonly User[];

  constructor(users: readonly User[]) {
    this.users = users;
  }

  public getUser(id: string): User | undefined {
    return findById(this.users, id);
  }
}

export const formatUser = (user: User): string => user.name.trim();
```

## Обработанный контекст

```text
<|file_sep|>src/users.ts
export interface User {
  id: string;
  name: string;
}

export const DEFAULT_LIMIT = 25;
export let resultLimit: number = DEFAULT_LIMIT;

export function findById<T extends { id: string }>(
  items: readonly T[],
  id: string,
): T | undefined;

export class UsersRepository {
  private readonly users: readonly User[];

  constructor(users: readonly User[]);
  public getUser(id: string): User | undefined;
}

export const formatUser = (user: User): string => {/* тело функции опущено */};
```
