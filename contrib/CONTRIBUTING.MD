# Contributing to Bittensor CLI
We want to make contributing to this project as easy and transparent as
possible.

We have an official Discord server where the community chimes in with helpful advice if you have questions. 
This is the fastest way to get an answer and the core development team is active on Discord.

* [Official Bittensor Discord](https://discord.gg/7wvFuPJZgq)
* [Bittensor Developers Group (Church of Rao)](https://discord.gg/4AMrc7B4)

## Issues
We use GitHub issues to track bugs and improvements. Please ensure your description is
clear and has sufficient instructions to be able to reproduce the issue.

When submitting a bug report, please include the following information:
1. Python version
2. Network or Chain which was being used to interact with Subtensor
3. Steps to reproduce the issue
4. Expected behavior and actual behavior
5. Any error messages or stack traces encountered

## Pull Requests
We welcome your pull requests. To ensure a smooth and efficient review process, please follow these guidelines:

### Bug fixes & Improvements:

1. Fork the repo and create your branch from `staging`.
2. If you've added code that should be tested, add tests.
3. If you've changed APIs, update the documentation.
4. Ensure the test suite passes.
5. Make sure your code lints. We use ruff for linting.
6. Provide detailed explanation what your PR fixes, alternate designs, and possible ripple effects.

### Feature Requests
We welcome feature requests and suggestions for improving the Bittensor CLI. To submit a feature request, please follow these guidelines:

1. If your feature request doesn't already exist, create a new issue on GitHub with the label "enhancement" or "feature request".
3. Provide a clear and descriptive title for the feature request.
4. Explain the feature you'd like to see added, why it would be useful, and how it could be implemented. Be as specific as possible.
5. If applicable, include examples, screenshots, or mockups to help illustrate your feature request.
6. Be patient and understanding. The maintainers will review your feature request and provide feedback. 


### Signed Commits

All commits in pull requests must be signed. We require signed commits to verify the authenticity of contributions and ensure code integrity.

To sign your commits, you must have GPG signing configured in Git:

```bash
git commit -S -m "your commit message"
```

Or configure Git to sign all commits automatically:

```bash
git config --global commit.gpgsign true
```

For instructions on setting up GPG key signing, see [GitHub's documentation on signing commits](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits).

> **Note:** Pull requests containing unsigned commits will not be merged.
