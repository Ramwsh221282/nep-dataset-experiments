# Rust

Обработанный контекст:

```text
<|file_sep|>src/users_repository.rs
impl UsersRepository {
    pub async fn get_user(&self, id: UserId) -> Result<User, RepositoryError>;
}

<|file_sep|>src/user.rs
pub struct User {
    pub id: UserId,
    pub name: String,
}
```
