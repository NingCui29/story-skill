namespace StoryWorkbench;

/// <summary>Only an unexposed page, or a page replaced after a verified navigation, can be discarded after failure.</summary>
public sealed class PageLifecycle
{
    public bool MayContainInput { get; private set; }
    public bool Loading { get; private set; } = true;
    public bool Failed { get; private set; }
    public bool CanDiscardFailed => Failed && !Loading && !MayContainInput;
    public void BeginVerifiedNavigation() { MayContainInput = false; Loading = true; Failed = false; }
    public void Complete(bool success)
    {
        Loading = false; Failed = !success;
        if (success) MayContainInput = true;
    }
    public void Fail() { Loading = false; Failed = true; }
    public void AllowInput() { MayContainInput = true; }
}
