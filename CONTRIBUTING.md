# Contributing to tt-auto-triage

Thank you for your interest in contributing to tt-auto-triage! We welcome contributions from the community.

## How to Contribute

### Reporting Bugs

If you find a bug, please report it by opening a [GitHub Issue](https://github.com/tenstorrent/tt-auto-triage/issues). Include:

- A clear description of the problem
- Steps to reproduce the issue
- Expected vs. actual behavior
- Your environment (OS, GitHub Actions runner version, etc.)
- Any relevant logs or error messages

### Suggesting Features

Feature requests are welcome! Please open a [GitHub Issue](https://github.com/tenstorrent/tt-auto-triage/issues) with:

- A clear description of the proposed feature
- The use case and why it would be valuable
- Any implementation ideas you have

### Submitting Pull Requests

We actively welcome pull requests for bug fixes and new features.

1. **Fork the repository** and create your branch from `main`
2. **Make your changes**:
   - Write clear, concise commit messages
   - Add tests if applicable
   - Update documentation as needed
3. **Test your changes**:
   - Ensure GitHub Actions workflows run successfully
   - Test the actions in a real workflow if possible
4. **Submit a pull request**:
   - Provide a clear description of what you've changed and why
   - Reference any related issues
   - Be responsive to review feedback

### Review Process

- Pull requests are reviewed on a **weekly basis**
- Maintainers may request changes or provide feedback
- Once approved, your PR will be merged by a maintainer

## Development Guidelines

### Code Style

- Follow Python best practices (PEP 8)
- Keep actions modular and reusable
- Document action inputs and outputs clearly
- Use meaningful variable names

### Testing

- Test your changes in a real GitHub Actions workflow before submitting
- Verify that error handling works correctly
- Check that Slack notifications format properly

### Documentation

- Update the README.md if you change functionality
- Document new action inputs/outputs in action.yml files
- Add comments for complex logic

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to ospo@tenstorrent.com.

## License

By contributing to tt-auto-triage, you agree that your contributions will be licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

## Questions?

If you have questions about contributing, feel free to open a GitHub Issue or reach out to the maintainers.

---

Thank you for helping make tt-auto-triage better!
