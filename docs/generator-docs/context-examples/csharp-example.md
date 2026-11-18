# C#

Обработанный контекст:

```text
public sealed class User {
  public Name
  public UserId

  public void Print()
}
```

```text
public sealed class UsersRepository {
  private readonly IUserRepository _userRepository;

  public async Task GetUser(Guid id)
  public async Task SaveUser(User user)
}
```
