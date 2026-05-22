# Takyon Secrets

Put local provider and vendor keys in `secrets/.env`.

The root launcher loads the active Takyon runtime with:

```text
TAKYON_HOME=/Users/Zygote/Downloads/takyon/.takyon
```

The Hermes Takyon plugin also looks for:

```text
/Users/Zygote/Downloads/takyon/secrets/.env
/Users/Zygote/Downloads/takyon/.takyon/secrets/.env
```

Do not commit secret values.
