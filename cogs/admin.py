import re
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from views import SetupView
from safety import safety_wrapper, financial_safety, InputValidator, SAFETY_CONFIG
from utils.errors import SmartErrorMessages


class AdminCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="setup",
        description="🛠️ Setup full market, paid channel, and bidding war system.",
    )
    @app_commands.describe(
        designated_channels="Ping or list IDs for all non-spam channels",
        paid_channel="Select or leave blank to auto-create unified paid messages & ads channel",
    )
    @app_commands.default_permissions(administrator=True)
    @safety_wrapper("admin")
    async def setup(
        self,
        interaction: discord.Interaction,
        designated_channels: str,
        paid_channel: discord.TextChannel = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        channel_ids = re.findall(r"<#(\d+)>", designated_channels)
        if not channel_ids:
            channel_ids = re.findall(r"\d+", designated_channels)
        channels_str = ",".join(channel_ids)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                send_messages=False, view_channel=True
            ),
            guild.me: discord.PermissionOverwrite(
                send_messages=True, view_channel=True
            ),
        }

        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM guild_config WHERE guild_id = ?", (guild.id,)
                )
                existing_cfg = cursor.fetchone()

            # Check existing channels - using direct index access for sqlite3.Row
            board_chan = None
            buy_chan = None
            sell_chan = None
            bids_chan = None
            loans_chan = None
            p_chan = None

            if existing_cfg:
                # Check if each column exists and has a value
                try:
                    if existing_cfg["board_channel_id"]:
                        board_chan = guild.get_channel(existing_cfg["board_channel_id"])
                except (KeyError, IndexError, TypeError):
                    pass
                
                try:
                    if existing_cfg["buy_channel_id"]:
                        buy_chan = guild.get_channel(existing_cfg["buy_channel_id"])
                except (KeyError, IndexError, TypeError):
                    pass
                
                try:
                    if existing_cfg["sell_channel_id"]:
                        sell_chan = guild.get_channel(existing_cfg["sell_channel_id"])
                except (KeyError, IndexError, TypeError):
                    pass
                
                try:
                    if existing_cfg["bids_channel_id"]:
                        bids_chan = guild.get_channel(existing_cfg["bids_channel_id"])
                except (KeyError, IndexError, TypeError):
                    pass
                
                try:
                    if existing_cfg["loans_channel_id"]:
                        loans_chan = guild.get_channel(existing_cfg["loans_channel_id"])
                except (KeyError, IndexError, TypeError):
                    pass
                
                try:
                    if existing_cfg["paid_channel_id"]:
                        p_chan = guild.get_channel(existing_cfg["paid_channel_id"])
                except (KeyError, IndexError, TypeError):
                    pass

            category = None
            if not all([board_chan, buy_chan, sell_chan, bids_chan]):
                category = await guild.create_category("📈 Stock Market")

            if not board_chan:
                board_chan = await guild.create_text_channel("stock-board", category=category, overwrites=overwrites)
            if not buy_chan:
                buy_chan = await guild.create_text_channel("buy-market", category=category, overwrites=overwrites)
            if not sell_chan:
                sell_chan = await guild.create_text_channel("sell-market", category=category, overwrites=overwrites)
            if not bids_chan:
                bids_chan = await guild.create_text_channel("bids", category=category, overwrites=overwrites)
            
            # Always create loans channel if it doesn't exist
            if not loans_chan:
                loans_chan = await guild.create_text_channel("loans", category=category, overwrites=overwrites)

            if not paid_channel:
                paid_channel = p_chan if p_chan else await guild.create_text_channel("paid-ads", category=category, overwrites=overwrites)

            # Create audit log channel for safety system
            audit_channel = None
            for channel in guild.channels:
                if channel.name == "audit-logs" and isinstance(channel, discord.TextChannel):
                    audit_channel = channel
                    break
            
            if not audit_channel:
                audit_overwrites = {
                    guild.default_role: discord.PermissionOverwrite(
                        view_channel=False
                    ),
                    guild.me: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True
                    ),
                }
                audit_channel = await guild.create_text_channel(
                    "audit-logs", 
                    category=category, 
                    overwrites=audit_overwrites
                )
                # Set the channel in safety config
                SAFETY_CONFIG["security_log_channel"] = audit_channel.id
                await audit_channel.send(
                    "🔒 **Audit Log Channel Created**\n"
                    "All administrative and financial actions will be logged here for security purposes."
                )

            # Get existing message IDs if available
            board_msg_id = None
            buy_msg_id = None
            sell_msg_id = None
            loans_msg_id = None

            if existing_cfg:
                try:
                    board_msg_id = existing_cfg["board_msg_id"]
                except (KeyError, IndexError, TypeError):
                    pass
                try:
                    buy_msg_id = existing_cfg["buy_msg_id"]
                except (KeyError, IndexError, TypeError):
                    pass
                try:
                    sell_msg_id = existing_cfg["sell_msg_id"]
                except (KeyError, IndexError, TypeError):
                    pass
                try:
                    loans_msg_id = existing_cfg["loans_msg_id"]
                except (KeyError, IndexError, TypeError):
                    pass

            # Only send initial messages if channels were just created
            if not existing_cfg or not board_msg_id:
                init_board = await board_chan.send("📊 **Initializing Live Stock Board...**")
                init_buy = await buy_chan.send("📈 **Initializing Buy Market...**")
                init_sell = await sell_chan.send("📉 **Initializing Sell Market...**")
                init_loans = await loans_chan.send("💳 **Loan Requests**\nUse `/request_loan` to request a loan from a company.")
            else:
                # Try to find existing messages
                try:
                    init_board = await board_chan.fetch_message(board_msg_id)
                except:
                    init_board = await board_chan.send("📊 **Initializing Live Stock Board...**")
                
                try:
                    init_buy = await buy_chan.fetch_message(buy_msg_id)
                except:
                    init_buy = await buy_chan.send("📈 **Initializing Buy Market...**")
                
                try:
                    init_sell = await sell_chan.fetch_message(sell_msg_id)
                except:
                    init_sell = await sell_chan.send("📉 **Initializing Sell Market...**")
                
                try:
                    init_loans = await loans_chan.fetch_message(loans_msg_id)
                except:
                    init_loans = await loans_chan.send("💳 **Loan Requests**\nUse `/request_loan` to request a loan from a company.")

            self.bot.MARKET_CHANNELS[guild.id] = {
                "board_channel_id": board_chan.id,
                "board_message_id": init_board.id,
                "buy_channel_id": buy_chan.id,
                "buy_message_id": init_buy.id,
                "sell_channel_id": sell_chan.id,
                "sell_message_id": init_sell.id,
                "loans_channel_id": loans_chan.id,
                "loans_message_id": init_loans.id,
                "paid_channel_id": paid_channel.id,
                "bids_channel_id": bids_chan.id,
                "designated_channels": channels_str,
            }

            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO guild_config (guild_id, designated_channels, board_channel_id, buy_channel_id, sell_channel_id, 
                        board_msg_id, buy_msg_id, sell_msg_id, paid_channel_id, bids_channel_id, loans_channel_id, loans_msg_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET 
                        designated_channels=?, board_channel_id=?, buy_channel_id=?, sell_channel_id=?, 
                        board_msg_id=?, buy_msg_id=?, sell_msg_id=?, paid_channel_id=?, bids_channel_id=?,
                        loans_channel_id=?, loans_msg_id=?
                """,
                    (
                        guild.id,
                        channels_str,
                        board_chan.id,
                        buy_chan.id,
                        sell_chan.id,
                        init_board.id,
                        init_buy.id,
                        init_sell.id,
                        paid_channel.id,
                        bids_chan.id,
                        loans_chan.id,
                        init_loans.id,
                        channels_str,
                        board_chan.id,
                        buy_chan.id,
                        sell_chan.id,
                        init_board.id,
                        init_buy.id,
                        init_sell.id,
                        paid_channel.id,
                        bids_chan.id,
                        loans_chan.id,
                        init_loans.id,
                    ),
                )
                conn.commit()

            embed = discord.Embed(
                title="📈 Market Setup",
                description=f"**Setup complete.** Existing channels preserved where available.\n\n🔒 **Audit Log Channel:** {audit_channel.mention}",
                color=discord.Color.gold(),
            )
            view = SetupView()
            await interaction.followup.send(
                embed=embed, view=view, ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Setup failed: {e}")

    @app_commands.command(
        name="register_party",
        description="Registers a new political party or state corporation.",
    )
    @app_commands.describe(
        party_id="Short unique ID for the party (e.g. corp)",
        name="Full display name of the party",
        role="The Discord role representing party members",
        manager_role="SHOULD BE FOR PARTY SPECIFIC people, NOT a manager of all parties.",
        shares="Starting Shares (Default 20)",
        tax="Enable taxation? (Y/N)",
        tax_percentage="Tax percentage (0-100)",
    )
    @app_commands.default_permissions(manage_roles=True)
    @safety_wrapper("admin")
    async def register_party(
        self,
        interaction: discord.Interaction,
        party_id: str,
        name: str,
        role: discord.Role,
        manager_role: discord.Role = None,
        shares: float = 20.0,
        tax: str = "N",
        tax_percentage: float = 0.0,
    ):
        await interaction.response.defer(ephemeral=True)
        
        # Safety: Validate party_id
        valid, msg = InputValidator.validate_party_id(party_id)
        if not valid:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return
        
        # Safety: Validate name
        valid, msg = InputValidator.validate_name(name)
        if not valid:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return
        
        # Safety: Validate shares
        valid, msg = InputValidator.validate_shares(shares)
        if not valid:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return
        
        clean_party_id = party_id.strip().lower()
        tax_enabled = 1 if tax.strip().upper() in ["Y", "YES"] else 0

        if not manager_role:
            manager_role = await interaction.guild.create_role(
                name=f"{name} Manager"
            )

        with get_db() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO parties (party_id, name, treasury, total_shares, tax_enabled, tax_percentage, role_id, manager_role_id, is_setup, is_company, structure_type) 
                    VALUES (?, ?, 50.0, ?, ?, ?, ?, ?, 1, 0, 'party')
                """,
                    (
                        clean_party_id,
                        name,
                        shares,
                        tax_enabled,
                        tax_percentage,
                        str(role.id),
                        str(manager_role.id),
                    ),
                )
                initial_price = 50.0 / shares if shares > 0 else 0.0
                cursor.execute(
                    "INSERT INTO stock_history (party_id, price) VALUES (?, ?)",
                    (clean_party_id, initial_price),
                )
                conn.commit()
                await interaction.followup.send(
                    f"✅ Successfully registered **{name}** (`{clean_party_id}`). Manager Role: {manager_role.mention}."
                )
            except sqlite3.IntegrityError:
                await interaction.followup.send(
                    SmartErrorMessages.already_exists(clean_party_id, "Party"),
                    ephemeral=True
                )

    @app_commands.command(
        name="manage_party",
        description="⚙️ Edit party properties or double party shares.",
    )
    @app_commands.describe(
        party_id="Target party ID",
        action="Action to perform",
        new_name="New party name (for edit_name action)",
        new_role="New party member role (for edit_role action)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(
                name="Double Shares (Splits all existing shares)",
                value="double_shares",
            ),
            app_commands.Choice(name="Edit Party Name", value="edit_name"),
            app_commands.Choice(name="Edit Party Member Role", value="edit_role"),
            app_commands.Choice(
                name="Delete Party (ADMIN ONLY)", value="delete_party"
            ),
        ]
    )
    @safety_wrapper("admin")
    async def manage_party(
        self,
        interaction: discord.Interaction,
        party_id: str,
        action: str,
        new_name: str = None,
        new_role: discord.Role = None,
    ):
        await interaction.response.defer(ephemeral=True)
        
        # Safety: Validate party_id
        valid, msg = InputValidator.validate_party_id(party_id)
        if not valid:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return
        
        clean_party_id = party_id.strip().lower()

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM parties WHERE party_id = ?", (clean_party_id,)
            )
            party = cursor.fetchone()

            if not party:
                await interaction.followup.send(
                    SmartErrorMessages.party_not_found(clean_party_id),
                    ephemeral=True
                )
                return

            is_admin = interaction.user.guild_permissions.administrator
            is_manager = party["manager_role_id"] and any(
                str(r.id) == party["manager_role_id"]
                for r in interaction.user.roles
            )

            if not (is_admin or is_manager):
                await interaction.followup.send(
                    SmartErrorMessages.permission_denied("manage this party"),
                    ephemeral=True
                )
                return

            if action == "delete_party":
                if not is_admin:
                    await interaction.followup.send(
                        "❌ Not even managers can delete parties. Only Admins can delete parties!"
                    )
                    return
                cursor.execute(
                    "DELETE FROM parties WHERE party_id = ?", (clean_party_id,)
                )
                cursor.execute(
                    "DELETE FROM shares WHERE party_id = ?", (clean_party_id,)
                )
                cursor.execute(
                    "DELETE FROM market_orders WHERE party_id = ?",
                    (clean_party_id,),
                )
                cursor.execute(
                    "DELETE FROM stock_history WHERE party_id = ?",
                    (clean_party_id,),
                )
                conn.commit()
                await interaction.followup.send(
                    f"🗑️ Party **{party['name']}** (`{clean_party_id}`) has been deleted."
                )

            elif action == "edit_name":
                if not new_name:
                    await interaction.followup.send(
                        "❌ Please provide a new name for the party using the `new_name` option."
                    )
                    return
                # Safety: Validate new name
                valid, msg = InputValidator.validate_name(new_name)
                if not valid:
                    await interaction.followup.send(f"❌ {msg}", ephemeral=True)
                    return
                cursor.execute(
                    "UPDATE parties SET name = ? WHERE party_id = ?",
                    (new_name, clean_party_id),
                )
                conn.commit()
                await interaction.followup.send(
                    f"✅ Renamed **{party['name']}** to **{new_name}**."
                )

            elif action == "edit_role":
                if not new_role:
                    await interaction.followup.send(
                        "❌ Please select a new role for party members using the `new_role` option."
                    )
                    return
                cursor.execute(
                    "UPDATE parties SET role_id = ? WHERE party_id = ?",
                    (str(new_role.id), clean_party_id),
                )
                conn.commit()
                await interaction.followup.send(
                    f"✅ Party member role for **{party['name']}** has been updated to {new_role.mention}.\n"
                    f"*(Manager role remains unchanged.)*"
                )

            elif action == "double_shares":
                cursor.execute(
                    "UPDATE parties SET total_shares = total_shares * 2 WHERE party_id = ?",
                    (clean_party_id,),
                )
                cursor.execute(
                    "UPDATE shares SET shares_owned = shares_owned * 2 WHERE party_id = ?",
                    (clean_party_id,),
                )
                cursor.execute(
                    "UPDATE market_orders SET shares_count = shares_count * 2 WHERE party_id = ?",
                    (clean_party_id,),
                )
                conn.commit()
                await interaction.followup.send(
                    f"📈 Doubled all shares for **{party['name']}**! Standard share price updated accordingly."
                )

    @app_commands.command(
        name="add_balance",
        description="💵 Manually adds money to a user's balance.",
    )
    @app_commands.default_permissions(administrator=True)
    @safety_wrapper("admin")
    @financial_safety(required_balance=False)
    async def add_balance(
        self, interaction: discord.Interaction, target: discord.User, amount: float
    ):
        # Safety: Validate amount
        valid, msg = InputValidator.validate_amount(amount, allow_zero=False)
        if not valid:
            await interaction.response.send_message(
                SmartErrorMessages.invalid_amount(amount),
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users (user_id, balance, message_count, premium_credits) VALUES (?, ?, 0, 0)
                ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?
            """,
                (target.id, amount, amount),
            )
            
            # Log transaction
            cursor.execute(
                """
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (target.id, "admin_add_balance", amount, "Admin added balance", None)
            )
            conn.commit()
        await interaction.response.send_message(
            f"✅ Added **${amount:.2f}** to {target.mention}.", ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(AdminCog(bot))