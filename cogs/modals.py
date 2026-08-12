import discord
from discord import app_commands
from discord.ext import commands
import datetime

class ConfirmTransactionModal(discord.ui.Modal, title="Confirm Transaction"):
    """Modal for confirming financial transactions to prevent accidental actions."""
    
    def __init__(self, action: str, amount: float, target: str, callback, command_name: str = None):
        super().__init__(timeout=120)
        self.action = action
        self.amount = amount
        self.target = target
        self.callback = callback
        self.command_name = command_name or action
        
    confirm = discord.ui.TextInput(
        label=f"Type 'CONFIRM' to proceed",
        placeholder="Type CONFIRM here...",
        required=True,
        min_length=6,
        max_length=7,
        style=discord.TextStyle.short
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.upper() != "CONFIRM":
            await interaction.response.send_message(
                "❌ Transaction cancelled. You must type 'CONFIRM' exactly.",
                ephemeral=True
            )
            return
        
        await self.callback(interaction)
    
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.response.send_message(
            f"❌ Error processing confirmation: {str(error)}",
            ephemeral=True
        )


class ConfirmPurchaseModal(discord.ui.Modal, title="Confirm Purchase"):
    """Modal for confirming stock purchases."""
    
    def __init__(self, party_name: str, shares: float, price: float, total_cost: float, callback):
        super().__init__(timeout=120)
        self.party_name = party_name
        self.shares = shares
        self.price = price
        self.total_cost = total_cost
        self.callback = callback
        # Set label dynamically in __init__
        self.confirm.label = f"Type 'CONFIRM' to buy {shares:.2f} shares"
        
    confirm = discord.ui.TextInput(
        placeholder="Type CONFIRM here...",
        required=True,
        min_length=6,
        max_length=7,
        style=discord.TextStyle.short
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.upper() != "CONFIRM":
            await interaction.response.send_message(
                "❌ Purchase cancelled. You must type 'CONFIRM' exactly.",
                ephemeral=True
            )
            return
        
        await self.callback(interaction)


class ConfirmSellModal(discord.ui.Modal, title="Confirm Sale"):
    """Modal for confirming stock sales."""
    
    def __init__(self, party_name: str, shares: float, price: float, total_value: float, callback):
        super().__init__(timeout=120)
        self.party_name = party_name
        self.shares = shares
        self.price = price
        self.total_value = total_value
        self.callback = callback
        # Set label dynamically in __init__
        self.confirm.label = f"Type 'CONFIRM' to sell {shares:.2f} shares"
        
    confirm = discord.ui.TextInput(
        placeholder="Type CONFIRM here...",
        required=True,
        min_length=6,
        max_length=7,
        style=discord.TextStyle.short
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.upper() != "CONFIRM":
            await interaction.response.send_message(
                "❌ Sale cancelled. You must type 'CONFIRM' exactly.",
                ephemeral=True
            )
            return
        
        await self.callback(interaction)


class ConfirmLoanRepaymentModal(discord.ui.Modal, title="Confirm Loan Repayment"):
    """Modal for confirming loan repayment."""
    
    def __init__(self, loan_id: int, amount: float, company_name: str, total_owed: float, callback):
        super().__init__(timeout=120)
        self.loan_id = loan_id
        self.amount = amount
        self.company_name = company_name
        self.total_owed = total_owed
        self.callback = callback
        # Set label dynamically in __init__
        self.confirm.label = f"Type 'CONFIRM' to repay ${total_owed:.2f}"
        
    confirm = discord.ui.TextInput(
        placeholder="Type CONFIRM here...",
        required=True,
        min_length=6,
        max_length=7,
        style=discord.TextStyle.short
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.upper() != "CONFIRM":
            await interaction.response.send_message(
                "❌ Repayment cancelled. You must type 'CONFIRM' exactly.",
                ephemeral=True
            )
            return
        
        await self.callback(interaction)