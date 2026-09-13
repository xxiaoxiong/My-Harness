"""Show ContextBuilder selecting a bounded view without changing State."""

from harness import (
    AgentState,
    CalculatorTool,
    ContextBuilder,
    MessageRole,
    ModelMessage,
)


def main() -> None:
    old_history = ModelMessage(
        MessageRole.ASSISTANT,
        "old reasoning " * 80,
    )
    middle_history = ModelMessage(
        MessageRole.USER,
        "old observation " * 80,
    )
    recent_history = ModelMessage(
        MessageRole.ASSISTANT,
        "latest decision " * 40,
    )
    state = AgentState.start(
        task_id="harn-06-demo",
        goal="Build a bounded model input",
        messages=[old_history, middle_history, recent_history],
    )
    state.begin_model_step()
    schemas = (CalculatorTool().schema,)

    fixed_only = AgentState.start(
        task_id=state.task_id,
        goal=state.goal,
        messages=[],
    )
    fixed_only.begin_model_step()
    fixed_size = ContextBuilder(max_context_chars=10_000).build(
        fixed_only,
        tool_schemas=schemas,
    ).total_chars
    budget = fixed_size + 120
    context = ContextBuilder(max_context_chars=budget).build(
        state,
        tool_schemas=schemas,
    )

    print("Agent State")
    print(f"  history_messages: {len(state.messages)} (unchanged)")
    print(f"  history_chars:    {sum(len(m.content) for m in state.messages)}")

    print("\nBounded Context")
    print(f"  max_context_chars: {context.max_context_chars}")
    print(f"  total_chars:       {context.total_chars}")
    print(f"  truncated:         {context.truncated}")
    print(f"  dropped_messages:  {context.dropped_messages}")
    print(f"  output_messages:   {len(context.messages)}")

    print("\nContext components")
    for message in context.messages:
        first_line = message.content.splitlines()[0]
        print(f"  {message.role.value:9} -> {first_line}")


if __name__ == "__main__":
    main()
