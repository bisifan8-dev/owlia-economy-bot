"""
Smart Error Messages for Economy Bot
Provides user-friendly error messages with recovery suggestions.
"""

class SmartErrorMessages:
    """Generate user-friendly error messages with helpful suggestions."""
    
    @staticmethod
    def insufficient_funds(balance: float, needed: float, command: str = None) -> str:
        """Generate error message for insufficient funds."""
        suggestions = []
        
        if balance < 1:
            suggestions.append("📝 Send **50 messages** in designated channels to earn **$1.00**")
            suggestions.append("💬 Each 50 messages = $1.00 payout")
        
        if balance > 0:
            suggestions.append(f"💰 Try reducing your amount (you have **${balance:.2f}**)")
        
        if command != "pay":
            suggestions.append("💸 Use `/pay` to receive money from other users")
        
        suggestions.append("🏦 Use `/invest` to grow your wealth through investments")
        suggestions.append("💳 Consider requesting a loan with `/request_loan`")
        
        return (
            f"❌ **Insufficient Funds!**\n"
            f"Balance: **${balance:.2f}**\n"
            f"Needed: **${needed:.2f}**\n\n"
            f"**💡 Suggestions:**\n" + 
            "\n".join(f"• {s}" for s in suggestions[:4])
        )
    
    @staticmethod
    def insufficient_shares(owned: float, needed: float, party_name: str) -> str:
        """Generate error message for insufficient shares."""
        return (
            f"❌ **Not Enough Shares!**\n"
            f"You have **{owned:.2f}** shares of **{party_name}**\n"
            f"Need: **{needed:.2f}** shares\n\n"
            f"**💡 Suggestions:**\n"
            f"• Use `/buy` to purchase more shares\n"
            f"• Use `/market` to find sellers\n"
            f"• Check if you're in the right company role\n"
            f"• Use `/company_info` to see your holdings"
        )
    
    @staticmethod
    def party_not_found(party_id: str) -> str:
        """Generate error message for party not found."""
        return (
            f"❌ **Entity Not Found!**\n"
            f"`{party_id}` doesn't exist or is misspelled.\n\n"
            f"**💡 Suggestions:**\n"
            f"• Check spelling (case insensitive)\n"
            f"• Use `/company_info` to see all entities\n"
            f"• Use `/market` to browse available stocks\n"
            f"• Contact an admin if you believe this is a mistake"
        )
    
    @staticmethod
    def already_exists(entity_id: str, entity_type: str = "Company") -> str:
        """Generate error message for duplicate entity."""
        return (
            f"❌ **{entity_type} Already Exists!**\n"
            f"`{entity_id}` is already registered.\n\n"
            f"**💡 Suggestions:**\n"
            f"• Choose a different ID\n"
            f"• Use `/company_info` to view existing entities\n"
            f"• Use `/manage_party` to edit existing entities"
        )
    
    @staticmethod
    def invalid_amount(amount: float, min_amount: float = 0.01, max_amount: float = 1000000.0) -> str:
        """Generate error message for invalid amount."""
        return (
            f"❌ **Invalid Amount!**\n"
            f"Amount: **${amount:.2f}**\n\n"
            f"**💡 Requirements:**\n"
            f"• Minimum: **${min_amount:.2f}**\n"
            f"• Maximum: **${max_amount:,.2f}**\n"
            f"• Must be greater than zero"
        )
    
    @staticmethod
    def permission_denied(action: str, required_role: str = None) -> str:
        """Generate error message for permission denied."""
        suggestions = []
        if required_role:
            suggestions.append(f"• You need the **{required_role}** role")
        suggestions.append("• Contact a server administrator")
        suggestions.append("• Use `/manage_company` to check your permissions")
        
        return (
            f"❌ **Permission Denied!**\n"
            f"You don't have permission to **{action}**.\n\n"
            f"**💡 Suggestions:**\n" + 
            "\n".join(suggestions)
        )
    
    @staticmethod
    def loan_not_found(loan_id: int) -> str:
        """Generate error message for loan not found."""
        return (
            f"❌ **Loan Not Found!**\n"
            f"Loan #{loan_id} doesn't exist or you don't have permission.\n\n"
            f"**💡 Suggestions:**\n"
            f"• Check the loan ID\n"
            f"• Use `/manage_loan` to see your loans\n"
            f"• Use `/company_info` to see company loans"
        )
    
    @staticmethod
    def insufficient_treasury(treasury: float, needed: float, company_name: str) -> str:
        """Generate error message for insufficient treasury."""
        return (
            f"❌ **Insufficient Treasury!**\n"
            f"**{company_name}** treasury: **${treasury:.2f}**\n"
            f"Needed: **${needed:.2f}**\n\n"
            f"**💡 Suggestions:**\n"
            f"• Use `/invest` to add funds to the company\n"
            f"• Reduce the amount requested\n"
            f"• The company needs more investors"
        )
    
    @staticmethod
    def already_voted() -> str:
        """Generate error message for already voted."""
        return (
            f"❌ **Already Voted!**\n"
            f"You've already cast your vote in this election.\n\n"
            f"**💡 Suggestions:**\n"
            f"• Wait for the election to conclude\n"
            f"• Check results with `/ceo_results`\n"
            f"• Each shareholder gets one vote per election"
        )
    
    @staticmethod
    def must_be_shareholder(company_name: str) -> str:
        """Generate error message for non-shareholder."""
        return (
            f"❌ **Must Be a Shareholder!**\n"
            f"You need to own shares in **{company_name}** to do this.\n\n"
            f"**💡 Suggestions:**\n"
            f"• Use `/buy` to purchase shares\n"
            f"• Use `/invest` to invest in the company\n"
            f"• Check your holdings with `/info`"
        )
    
    @staticmethod
    def rate_limited(time_remaining: float) -> str:
        """Generate error message for rate limiting."""
        return (
            f"⚠️ **Rate Limited!**\n"
            f"Please wait **{time_remaining:.1f}** seconds before trying again.\n\n"
            f"**💡 Suggestions:**\n"
            f"• Slow down your commands\n"
            f"• Financial commands are limited to prevent spam\n"
            f"• This limit resets automatically"
        )
    
    @staticmethod
    def command_error(error: str, command: str = None) -> str:
        """Generate generic error message for command errors."""
        suggestions = []
        if command:
            suggestions.append(f"• Try `/help {command}` for usage")
        suggestions.append("• Check your inputs and try again")
        suggestions.append("• Contact an administrator if this persists")
        
        return (
            f"❌ **Command Error!**\n"
            f"{error}\n\n"
            f"**💡 Suggestions:**\n" + 
            "\n".join(suggestions)
        )