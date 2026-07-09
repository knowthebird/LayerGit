# Security Policy

LayerGit is experimental software that modifies local working trees only through
explicit commands.

Please report path traversal, unintended source modification, data loss, unsafe
Git-index behavior, or accidental commit/push behavior as security issues.

LayerGit should never commit or push for the user during normal layer,
compose, apply, delete, or selection workflows.
