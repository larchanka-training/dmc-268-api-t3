# LLM Review Generation

## Purpose

Describe structured review generation and provider fallback through the LLM gateway.

## Requirements

### Requirement: Generate a structured review from a diff
The review generation function SHALL build a prompt from the diff, optional language, optional commit message, optional few-shot examples, and token limit, then return a validated `ReviewResult` from the LLM gateway.

#### Scenario: Generate review with optional context
- **WHEN** a caller supplies a diff and any supported optional context
- **THEN** the function builds the review prompt and returns the gateway's `ReviewResult`

### Requirement: Fall back across configured LLM providers
The LLM gateway SHALL try providers in configured order and return the first successful structured result.

#### Scenario: Earlier provider fails
- **WHEN** a provider raises an error while generating or validating a result
- **THEN** the gateway tries the next configured provider

#### Scenario: All providers fail
- **WHEN** every configured provider fails
- **THEN** the gateway raises `AllProvidersFailedError` containing provider failure details

#### Scenario: No provider is configured
- **WHEN** a gateway is constructed with an empty provider list
- **THEN** construction fails with `ValueError`
