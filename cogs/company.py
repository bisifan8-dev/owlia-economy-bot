import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import get_db, get_user_managed_parties, is_company_shareholder, get_user_share_weight
import datetime


class CompanyCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.process_loans.start()

    def cog_unload(self):
        self.process_loans.cancel()

    async def sync_company_permissions(self, company_id: str, guild: discord.Guild):
        """Sync company channel permissions based on shareholders."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM parties WHERE party_id = ?", (company_id,))
            company = cursor.fetchone()
            if not company or not company["is_company"]:
                return

            cursor.execute("SELECT user_id, shares_owned FROM shares WHERE party_id = ? AND shares_owned > 0", (company_id,))
            shareholders = cursor.fetchall()

            # Get CEO info (seat 0)
            cursor.execute("SELECT user_id FROM board_members WHERE company_id = ? AND seat_number = 0", (company_id,))
            ceo_row = cursor.fetchone()
            ceo_id = ceo_row["user_id"] if ceo_row else None

        role = guild.get_role(int(company["role_id"])) if company["role_id"] else None
        chat_chan = guild.get_channel(company["chat_channel_id"])
        vote_chan = guild.get_channel(company["vote_channel_id"])

        if chat_chan and role:
            await chat_chan.set_permissions(guild.default_role, view_channel=False)
            await chat_chan.set_permissions(role, view_channel=True, send_messages=True)

        if vote_chan:
            await vote_chan.set_permissions(guild.default_role, view_channel=False)
            for s in shareholders:
                member = guild.get_member(s["user_id"])
                if member:
                    await vote_chan.set_permissions(member, view_channel=True, send_messages=False)
            
            # CEO can send in vote channel
            if ceo_id:
                ceo_member = guild.get_member(ceo_id)
                if ceo_member:
                    await vote_chan.set_permissions(ceo_member, view_channel=True, send_messages=True)

    @app_commands.command(
        name="register_company",
        description="🏬 Create a new company with simplified structure."
    )
    @app_commands.describe(
        company_id="Unique identifier ID (e.g. techcorp)",
        name="Full company display name",
        structure="Company structural type",
        total_shares="Total initial shares (default varies by structure)"
    )
    @app_commands.choices(
        structure=[
            app_commands.Choice(name="Sole Proprietorship (1 Owner)", value="sole_proprietorship"),
            app_commands.Choice(name="Partnership (2-5 Partners)", value="partnership"),
            app_commands.Choice(name="Corporation (20 Shares, CEO-led)", value="corporation"),
        ]
    )
    async def register_company(
        self,
        interaction: discord.Interaction,
        company_id: str,
        name: str,
        structure: str,
        total_shares: float = None
    ):
        await interaction.response.defer(ephemeral=True)
        clean_id = company_id.strip().lower()

        # Set default shares based on structure
        if total_shares is None:
            if structure == "sole_proprietorship":
                total_shares = 5.0
            elif structure == "partnership":
                total_shares = 10.0
            else:  # corporation
                total_shares = 20.0

        if total_shares <= 0:
            await interaction.followup.send("❌ Total shares must be greater than zero.")
            return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT party_id FROM parties WHERE party_id = ?", (clean_id,))
            if cursor.fetchone():
                await interaction.followup.send("❌ Company/Party ID already exists.")
                return

            # Create roles
            company_role = await interaction.guild.create_role(name=f"{name} Member")
            manager_role = await interaction.guild.create_role(name=f"{name} Manager")

            # For sole proprietorship, founder gets 100% of shares immediately
            founder_shares = total_shares if structure == "sole_proprietorship" else 0.0

            # max_positions: 0 for no board, 1 for CEO seat
            max_positions = 1

            cursor.execute(
                """
                INSERT INTO parties (party_id, name, treasury, total_shares, tax_enabled, tax_percentage, 
                    role_id, manager_role_id, is_setup, is_company, structure_type, creation_pending, 
                    initial_invested, max_positions)
                VALUES (?, ?, 0.0, ?, 0, 0.0, ?, ?, 0, 1, ?, 1, 0.0, ?)
                """,
                (clean_id, name, total_shares, str(company_role.id), str(manager_role.id), structure, max_positions)
            )

            # If sole proprietorship, give founder all shares and set as CEO
            if structure == "sole_proprietorship":
                cursor.execute(
                    """
                    INSERT INTO shares (user_id, party_id, shares_owned) VALUES (?, ?, ?)
                    """,
                    (interaction.user.id, clean_id, founder_shares)
                )
                # Set as CEO (seat 0)
                cursor.execute(
                    """
                    INSERT INTO board_members (company_id, user_id, seat_number) VALUES (?, ?, 0)
                    """,
                    (clean_id, interaction.user.id)
                )
                # Add initial price to history
                cursor.execute(
                    "INSERT INTO stock_history (party_id, price) VALUES (?, ?)",
                    (clean_id, 0.0)
                )
                conn.commit()

                embed = discord.Embed(
                    title="✅ Company Created!",
                    description=(
                        f"**{name}** (`{clean_id}`) has been registered as a **Sole Proprietorship**!\n\n"
                        f"• You own **{founder_shares:.2f}** shares (100%)\n"
                        f"• You are the CEO\n"
                        f"• Treasury starts at **$0.00**\n"
                        f"• Use `/invest` to add funds to treasury"
                    ),
                    color=discord.Color.green()
                )
                await interaction.followup.send(embed=embed)
            else:
                conn.commit()
                embed = discord.Embed(
                    title="⌛ Company Created - Awaiting Investment",
                    description=(
                        f"**{name}** (`{clean_id}`) has been registered as a **{structure.replace('_', ' ').title()}**!\n\n"
                        f"• Total shares: **{total_shares:.2f}**\n"
                        f"• Treasury starts at **$0.00**\n"
                        f"• Use `/invest` to add funds to treasury\n"
                        f"• Share price = Treasury / {total_shares:.2f}\n\n"
                        f"**Partnership Note:** {structure == 'partnership' and 'Requires 2-5 partners' or 'Anyone can invest'}"
                    ),
                    color=discord.Color.gold()
                )
                await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="invest",
        description="💵 Contribute capital to a company (anyone can invest)."
    )
    @app_commands.describe(
        company_id="Target company ID",
        amount="Capital contribution amount"
    )
    async def invest(self, interaction: discord.Interaction, company_id: str, amount: float):
        await interaction.response.defer(ephemeral=True)
        clean_id = company_id.strip().lower()

        if amount <= 0:
            await interaction.followup.send("❌ Investment must be greater than zero.")
            return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM parties WHERE party_id = ? AND is_company = 1", (clean_id,))
            company = cursor.fetchone()

            if not company:
                await interaction.followup.send(f"❌ Company `{clean_id}` not found.")
                return

            cursor.execute("SELECT balance FROM users WHERE user_id = ?", (interaction.user.id,))
            u_row = cursor.fetchone()
            bal = u_row["balance"] if u_row else 0.0

            if bal < amount:
                await interaction.followup.send(f"❌ Insufficient funds! Balance: **${bal:.2f}**.")
                return

            # Update user balance
            cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, interaction.user.id))
            
            # Update company treasury
            cursor.execute("UPDATE parties SET treasury = treasury + ? WHERE party_id = ?", (amount, clean_id))
            
            # Track investment
            cursor.execute(
                """
                INSERT INTO company_investments (company_id, user_id, amount) VALUES (?, ?, ?)
                ON CONFLICT(company_id, user_id) DO UPDATE SET amount = amount + ?
                """,
                (clean_id, interaction.user.id, amount, amount)
            )

            # Calculate shares issued based on current price or proportional
            cursor.execute("SELECT treasury, total_shares FROM parties WHERE party_id = ?", (clean_id,))
            p_info = cursor.fetchone()
            
            total_treasury = p_info["treasury"]
            total_shares = p_info["total_shares"]
            
            # If there's no treasury yet, first investment gets all shares
            if total_treasury == amount:  # First investment
                shares_owned = total_shares
            else:
                # Proportional shares based on investment / new treasury * total shares
                shares_owned = (amount / total_treasury) * total_shares
            
            cursor.execute(
                """
                INSERT INTO shares (user_id, party_id, shares_owned) VALUES (?, ?, ?)
                ON CONFLICT(user_id, party_id) DO UPDATE SET shares_owned = shares_owned + ?
                """,
                (interaction.user.id, clean_id, shares_owned, shares_owned)
            )

            # Update stock history
            new_price = total_treasury / total_shares if total_shares > 0 else 0.0
            cursor.execute("INSERT INTO stock_history (party_id, price) VALUES (?, ?)", (clean_id, new_price))
            
            conn.commit()

        # Check if partnership and need to set CEO if first partner
        if company["structure_type"] == "partnership":
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as count FROM shares WHERE party_id = ? AND shares_owned > 0", (clean_id,))
                shareholder_count = cursor.fetchone()["count"]
                
                if shareholder_count == 1:
                    # First partner becomes CEO
                    cursor.execute(
                        "INSERT INTO board_members (company_id, user_id, seat_number) VALUES (?, ?, 0)",
                        (clean_id, interaction.user.id)
                    )
                    conn.commit()
                    ceo_msg = "\n🎉 You are the first investor and have been appointed CEO!"
                elif shareholder_count >= 2 and shareholder_count <= 5:
                    ceo_msg = f"\n📋 Partnership now has {shareholder_count}/5 partners."
                else:
                    ceo_msg = ""

        await interaction.followup.send(
            f"✅ Invested **${amount:.2f}** into **{company['name']}**!\n"
            f"• Received **{shares_owned:.2f}** shares\n"
            f"• Share price: **${new_price:.2f}**\n"
            f"• Treasury: **${total_treasury:.2f}**{ceo_msg if company['structure_type'] == 'partnership' else ''}"
        )

    @app_commands.command(
        name="company_info",
        description="ℹ️ View company details, shares, and ownership."
    )
    @app_commands.describe(
        company_id="Target company ID"
    )
    async def company_info(self, interaction: discord.Interaction, company_id: str):
        await interaction.response.defer(ephemeral=True)
        clean_id = company_id.strip().lower()

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM parties WHERE party_id = ? AND is_company = 1",
                (clean_id,)
            )
            company = cursor.fetchone()
            
            if not company:
                await interaction.followup.send(f"❌ Company `{clean_id}` not found.")
                return

            # Get shareholders
            cursor.execute(
                "SELECT user_id, shares_owned FROM shares WHERE party_id = ? AND shares_owned > 0 ORDER BY shares_owned DESC",
                (clean_id,)
            )
            shareholders = cursor.fetchall()

            # Get CEO (seat 0)
            cursor.execute(
                "SELECT user_id FROM board_members WHERE company_id = ? AND seat_number = 0",
                (clean_id,)
            )
            ceo_row = cursor.fetchone()
            ceo_id = ceo_row["user_id"] if ceo_row else None

        price = company["treasury"] / company["total_shares"] if company["total_shares"] > 0 else 0.0
        shareholder_count = len(shareholders)

        embed = discord.Embed(
            title=f"🏢 {company['name']}",
            description=f"**ID:** `{clean_id}`\n**Type:** {company['structure_type'].replace('_', ' ').title()}",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="💰 Financials",
            value=f"Treasury: **${company['treasury']:.2f}**\nShare Price: **${price:.2f}**\nTotal Shares: **{company['total_shares']:.2f}**",
            inline=True
        )
        
        embed.add_field(
            name="👥 Ownership",
            value=f"Shareholders: **{shareholder_count}**\nCEO: {f'<@{ceo_id}>' if ceo_id else '*Not elected*'}",
            inline=True
        )

        # Show top shareholders
        if shareholders:
            top_shares = shareholders[:5]
            share_text = ""
            for s in top_shares:
                member = interaction.guild.get_member(s["user_id"])
                name = member.display_name if member else f"User {s['user_id']}"
                pct = (s["shares_owned"] / company["total_shares"] * 100) if company["total_shares"] > 0 else 0
                share_text += f"• {name}: **{s['shares_owned']:.2f}** ({pct:.1f}%)\n"
            embed.add_field(
                name="📊 Top Shareholders",
                value=share_text or "*No shareholders yet*",
                inline=False
            )

        # Show partnership status if applicable
        if company["structure_type"] == "partnership":
            partner_count = shareholder_count
            status = f"{partner_count}/5 partners"
            if partner_count < 2:
                status += " ⚠️ Needs at least 2 partners"
            elif partner_count == 5:
                status += " ✅ Full"
            embed.add_field(
                name="🤝 Partnership Status",
                value=status,
                inline=False
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="vote_ceo",
        description="🗳️ Vote for CEO of a company (shareholders only, 1 vote per person)."
    )
    @app_commands.describe(
        company_id="Target company ID",
        candidate="User to vote for (must be a shareholder)"
    )
    async def vote_ceo(
        self,
        interaction: discord.Interaction,
        company_id: str,
        candidate: discord.Member
    ):
        await interaction.response.defer(ephemeral=True)
        clean_id = company_id.strip().lower()

        # Check if voter is a shareholder
        if not is_company_shareholder(interaction.user.id, clean_id):
            await interaction.followup.send("❌ You must be a shareholder to vote for CEO.")
            return

        # Check if candidate is a shareholder
        if not is_company_shareholder(candidate.id, clean_id):
            await interaction.followup.send(f"❌ {candidate.mention} must be a shareholder to be CEO.")
            return

        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if company exists
            cursor.execute(
                "SELECT name FROM parties WHERE party_id = ? AND is_company = 1",
                (clean_id,)
            )
            company = cursor.fetchone()
            if not company:
                await interaction.followup.send(f"❌ Company `{clean_id}` not found.")
                return

            # Check if user already voted
            cursor.execute(
                "SELECT * FROM board_votes WHERE election_id = 0 AND user_id = ? AND company_id = ?",
                (interaction.user.id, clean_id)
            )
            if cursor.fetchone():
                await interaction.followup.send("❌ You've already voted in this CEO election.")
                return

            # Record vote (using election_id=0 for CEO votes)
            vote_weight = get_user_share_weight(interaction.user.id, clean_id)
            
            # Store candidate in board_candidates with election_id=0
            cursor.execute(
                """
                INSERT INTO board_candidates (election_id, user_id) VALUES (0, ?)
                ON CONFLICT(election_id, user_id) DO NOTHING
                """,
                (candidate.id,)
            )
            
            # Get candidate_id
            cursor.execute(
                "SELECT candidate_id FROM board_candidates WHERE election_id = 0 AND user_id = ?",
                (candidate.id,)
            )
            candidate_row = cursor.fetchone()
            
            if not candidate_row:
                await interaction.followup.send("❌ Error registering candidate.")
                return

            # Record vote
            cursor.execute(
                """
                INSERT INTO board_votes (election_id, candidate_id, user_id, weight, company_id)
                VALUES (0, ?, ?, ?, ?)
                """,
                (candidate_row["candidate_id"], interaction.user.id, vote_weight, clean_id)
            )
            conn.commit()

        await interaction.followup.send(f"✅ Cast your vote for {candidate.mention} with **{vote_weight:.2f}** share weight!")

    @app_commands.command(
        name="ceo_results",
        description="📊 Show current CEO election results for a company."
    )
    @app_commands.describe(
        company_id="Target company ID"
    )
    async def ceo_results(
        self,
        interaction: discord.Interaction,
        company_id: str
    ):
        await interaction.response.defer(ephemeral=True)
        clean_id = company_id.strip().lower()

        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT name FROM parties WHERE party_id = ? AND is_company = 1",
                (clean_id,)
            )
            company = cursor.fetchone()
            if not company:
                await interaction.followup.send(f"❌ Company `{clean_id}` not found.")
                return

            # Get vote results
            cursor.execute(
                """
                SELECT c.user_id, COALESCE(SUM(v.weight), 0) as total_votes
                FROM board_candidates c
                LEFT JOIN board_votes v ON c.candidate_id = v.candidate_id AND v.company_id = ?
                WHERE c.election_id = 0
                GROUP BY c.candidate_id, c.user_id
                ORDER BY total_votes DESC
                """,
                (clean_id,)
            )
            results = cursor.fetchall()

        embed = discord.Embed(
            title=f"🗳️ CEO Election Results - {company['name']}",
            color=discord.Color.gold()
        )

        if not results:
            embed.description = "*No votes cast yet. Use `/vote_ceo` to vote!*"
        else:
            result_text = ""
            total_votes = sum(r["total_votes"] for r in results)
            for i, r in enumerate(results):
                member = interaction.guild.get_member(r["user_id"])
                name = member.mention if member else f"User {r['user_id']}"
                pct = (r["total_votes"] / total_votes * 100) if total_votes > 0 else 0
                prefix = "👑 " if i == 0 and r["total_votes"] > 0 else ""
                result_text += f"{prefix}• {name}: **{r['total_votes']:.2f}** votes ({pct:.1f}%)\n"
            
            embed.add_field(
                name="📊 Results",
                value=result_text,
                inline=False
            )

            # Show current CEO
            cursor.execute(
                "SELECT user_id FROM board_members WHERE company_id = ? AND seat_number = 0",
                (clean_id,)
            )
            ceo = cursor.fetchone()
            if ceo:
                embed.set_footer(text=f"Current CEO: <@{ceo['user_id']}>")

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="request_loan",
        description="💳 Request a loan from a company."
    )
    @app_commands.describe(
        company_id="Target company ID",
        amount="Loan amount requested",
        duration_hours="Duration in hours to repay",
        interest_rate="Interest rate % (optional, default 0)"
    )
    async def request_loan(
        self,
        interaction: discord.Interaction,
        company_id: str,
        amount: float,
        duration_hours: int,
        interest_rate: float = 0.0
    ):
        await interaction.response.defer(ephemeral=True)
        clean_id = company_id.strip().lower()

        if amount <= 0:
            await interaction.followup.send("❌ Loan amount must be greater than zero.")
            return

        if duration_hours < 1 or duration_hours > 720:  # Max 30 days
            await interaction.followup.send("❌ Duration must be between 1 and 720 hours (30 days).")
            return

        if interest_rate < 0 or interest_rate > 100:
            await interaction.followup.send("❌ Interest rate must be between 0 and 100%.")
            return

        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check company exists and has enough treasury
            cursor.execute(
                "SELECT name, treasury FROM parties WHERE party_id = ? AND is_company = 1",
                (clean_id,)
            )
            company = cursor.fetchone()
            if not company:
                await interaction.followup.send(f"❌ Company `{clean_id}` not found.")
                return

            if company["treasury"] < amount:
                await interaction.followup.send(f"❌ Company treasury insufficient. Has **${company['treasury']:.2f}**, needs **${amount:.2f}**.")
                return

            # Check if user already has pending loan with this company
            cursor.execute(
                """
                SELECT * FROM loan_requests 
                WHERE company_id = ? AND user_id = ? AND status IN ('PENDING', 'APPROVED')
                """,
                (clean_id, interaction.user.id)
            )
            if cursor.fetchone():
                await interaction.followup.send("❌ You already have a pending or active loan with this company.")
                return

            # Create loan request
            cursor.execute(
                """
                INSERT INTO loan_requests (company_id, user_id, amount, duration_hours, interest_rate, status)
                VALUES (?, ?, ?, ?, ?, 'PENDING')
                """,
                (clean_id, interaction.user.id, amount, duration_hours, interest_rate)
            )
            request_id = cursor.lastrowid
            conn.commit()

        # Get loans channel
        guild_data = self.bot.MARKET_CHANNELS.get(interaction.guild_id, {})
        loans_channel_id = guild_data.get("loans_channel_id")
        loans_channel = interaction.guild.get_channel(loans_channel_id) if loans_channel_id else None

        if loans_channel:
            embed = discord.Embed(
                title=f"💳 Loan Request #{request_id}",
                description=f"**Company:** {company['name']}\n**Borrower:** {interaction.user.mention}\n**Amount:** ${amount:.2f}\n**Duration:** {duration_hours} hours\n**Interest:** {interest_rate}%",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="📋 Status",
                value="Pending approval",
                inline=False
            )
            await loans_channel.send(embed=embed)

        await interaction.followup.send(f"✅ Loan request #{request_id} submitted for **${amount:.2f}** from **{company['name']}**!")

    @app_commands.command(
        name="approve_loan",
        description="✅ Approve a pending loan request (Company Managers only)."
    )
    @app_commands.describe(
        request_id="Loan request ID",
        approve="True to approve, False to reject"
    )
    async def approve_loan(
        self,
        interaction: discord.Interaction,
        request_id: int,
        approve: bool
    ):
        await interaction.response.defer(ephemeral=True)

        with get_db() as conn:
            cursor = conn.cursor()
            
            # Get loan request
            cursor.execute(
                """
                SELECT l.*, p.name, p.manager_role_id, p.treasury
                FROM loan_requests l
                JOIN parties p ON l.company_id = p.party_id
                WHERE l.request_id = ?
                """,
                (request_id,)
            )
            loan = cursor.fetchone()
            
            if not loan:
                await interaction.followup.send(f"❌ Loan request #{request_id} not found.")
                return

            if loan["status"] != "PENDING":
                await interaction.followup.send(f"❌ Loan request #{request_id} is already {loan['status']}.")
                return

            # Check if user is a manager
            is_admin = interaction.user.guild_permissions.administrator
            is_manager = loan["manager_role_id"] and any(
                str(r.id) == loan["manager_role_id"]
                for r in interaction.user.roles
            )

            if not (is_admin or is_manager):
                await interaction.followup.send("❌ You don't have permission to approve loans for this company.")
                return

            if approve:
                if loan["treasury"] < loan["amount"]:
                    await interaction.followup.send(f"❌ Company treasury insufficient. Has **${loan['treasury']:.2f}**, needs **${loan['amount']:.2f}**.")
                    return

                # Calculate due time
                now = datetime.datetime.utcnow()
                due_time = now + datetime.timedelta(hours=loan["duration_hours"])

                # Update loan status
                cursor.execute(
                    """
                    UPDATE loan_requests 
                    SET status = 'APPROVED', approved_time = ?, due_time = ?, approved_by = ?
                    WHERE request_id = ?
                    """,
                    (now.strftime("%Y-%m-%d %H:%M:%S"), due_time.strftime("%Y-%m-%d %H:%M:%S"), interaction.user.id, request_id)
                )

                # Transfer money to user
                loan_amount = loan["amount"]
                cursor.execute(
                    "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (loan_amount, loan["user_id"])
                )
                
                # Deduct from company treasury
                cursor.execute(
                    "UPDATE parties SET treasury = treasury - ? WHERE party_id = ?",
                    (loan_amount, loan["company_id"])
                )

                conn.commit()

                await interaction.followup.send(
                    f"✅ Loan #{request_id} approved! {loan_amount:.2f} transferred to <@{loan['user_id']}>."
                )

                # Notify user
                try:
                    user = await self.bot.fetch_user(loan["user_id"])
                    await user.send(
                        f"✅ Your loan of **${loan_amount:.2f}** from **{loan['name']}** has been approved!\n"
                        f"Due date: <t:{int(due_time.timestamp())}:R>\n"
                        f"Amount to repay: **${loan_amount + (loan_amount * loan['interest_rate'] / 100):.2f}** (including {loan['interest_rate']}% interest)"
                    )
                except:
                    pass

            else:
                # Reject loan
                cursor.execute(
                    """
                    UPDATE loan_requests SET status = 'REJECTED', approved_by = ?
                    WHERE request_id = ?
                    """,
                    (interaction.user.id, request_id)
                )
                conn.commit()
                await interaction.followup.send(f"❌ Loan #{request_id} rejected.")

    @app_commands.command(
        name="manage_loan",
        description="📋 View and manage your loan requests."
    )
    @app_commands.describe(
        request_id="Specific loan ID to view (optional)"
    )
    async def manage_loan(
        self,
        interaction: discord.Interaction,
        request_id: int = None
    ):
        await interaction.response.defer(ephemeral=True)

        with get_db() as conn:
            cursor = conn.cursor()
            
            if request_id:
                cursor.execute(
                    """
                    SELECT l.*, p.name as company_name
                    FROM loan_requests l
                    JOIN parties p ON l.company_id = p.party_id
                    WHERE l.request_id = ? AND (l.user_id = ? OR l.approved_by = ? OR ? IN (
                        SELECT user_id FROM board_members WHERE company_id = l.company_id AND seat_number = 0
                    ))
                    """,
                    (request_id, interaction.user.id, interaction.user.id, interaction.user.id)
                )
                loan = cursor.fetchone()
                
                if not loan:
                    await interaction.followup.send(f"❌ Loan #{request_id} not found or you don't have permission.")
                    return

                embed = discord.Embed(
                    title=f"💳 Loan #{loan['request_id']}",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="📋 Details",
                    value=(
                        f"**Company:** {loan['company_name']}\n"
                        f"**Borrower:** <@{loan['user_id']}>\n"
                        f"**Amount:** ${loan['amount']:.2f}\n"
                        f"**Status:** {loan['status']}\n"
                        f"**Interest:** {loan['interest_rate']}%"
                    ),
                    inline=False
                )
                
                if loan["status"] == "APPROVED":
                    due = datetime.datetime.strptime(loan["due_time"], "%Y-%m-%d %H:%M:%S")
                    total_repay = loan["amount"] + (loan["amount"] * loan["interest_rate"] / 100)
                    embed.add_field(
                        name="⏰ Repayment",
                        value=f"Due: <t:{int(due.timestamp())}:R>\nTotal: **${total_repay:.2f}**",
                        inline=False
                    )
                
                await interaction.followup.send(embed=embed)
            else:
                # Show all user's loans
                cursor.execute(
                    """
                    SELECT l.*, p.name as company_name
                    FROM loan_requests l
                    JOIN parties p ON l.company_id = p.party_id
                    WHERE l.user_id = ?
                    ORDER BY l.request_time DESC
                    """,
                    (interaction.user.id,)
                )
                loans = cursor.fetchall()

                if not loans:
                    await interaction.followup.send("📭 You have no loan requests.")
                    return

                embed = discord.Embed(
                    title="💳 Your Loan Requests",
                    color=discord.Color.blue()
                )

                for l in loans[:10]:
                    status_emoji = {
                        "PENDING": "⏳",
                        "APPROVED": "✅",
                        "REJECTED": "❌",
                        "REPAID": "💚",
                        "DEFAULTED": "💀"
                    }.get(l["status"], "❓")
                    
                    due_text = ""
                    if l["status"] == "APPROVED" and l["due_time"]:
                        due = datetime.datetime.strptime(l["due_time"], "%Y-%m-%d %H:%M:%S")
                        due_text = f" | Due <t:{int(due.timestamp())}:R>"
                    
                    embed.add_field(
                        name=f"#{l['request_id']} - {l['company_name']}",
                        value=f"{status_emoji} **${l['amount']:.2f}** ({l['status']}){due_text}",
                        inline=False
                    )

                await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="repay_loan",
        description="💰 Repay an approved loan early."
    )
    @app_commands.describe(
        request_id="Loan ID to repay"
    )
    async def repay_loan(
        self,
        interaction: discord.Interaction,
        request_id: int
    ):
        await interaction.response.defer(ephemeral=True)

        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT l.*, p.name as company_name, p.treasury
                FROM loan_requests l
                JOIN parties p ON l.company_id = p.party_id
                WHERE l.request_id = ? AND l.user_id = ?
                """,
                (request_id, interaction.user.id)
            )
            loan = cursor.fetchone()
            
            if not loan:
                await interaction.followup.send(f"❌ Loan #{request_id} not found or not yours.")
                return

            if loan["status"] != "APPROVED":
                await interaction.followup.send(f"❌ Loan #{request_id} is not approved (status: {loan['status']}).")
                return

            # Calculate total owed with interest
            total_owed = loan["amount"] + (loan["amount"] * loan["interest_rate"] / 100)

            # Check user balance
            cursor.execute("SELECT balance FROM users WHERE user_id = ?", (interaction.user.id,))
            user_row = cursor.fetchone()
            balance = user_row["balance"] if user_row else 0.0

            if balance < total_owed:
                await interaction.followup.send(
                    f"❌ Insufficient funds! You have **${balance:.2f}**, need **${total_owed:.2f}** (including {loan['interest_rate']}% interest)."
                )
                return

            # Process repayment
            cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (total_owed, interaction.user.id))
            cursor.execute("UPDATE parties SET treasury = treasury + ? WHERE party_id = ?", (total_owed, loan["company_id"]))
            cursor.execute(
                """
                INSERT INTO loan_payments (request_id, user_id, amount) VALUES (?, ?, ?)
                """,
                (request_id, interaction.user.id, total_owed)
            )
            cursor.execute(
                "UPDATE loan_requests SET status = 'REPAID' WHERE request_id = ?",
                (request_id,)
            )
            conn.commit()

        await interaction.followup.send(
            f"✅ Loan #{request_id} repaid! Paid **${total_owed:.2f}** to **{loan['company_name']}** (including {loan['interest_rate']}% interest)."
        )

    @app_commands.command(
        name="delete_company",
        description="🗑️ Delete a company (admin for sole/corp, all partners for partnership)."
    )
    @app_commands.describe(
        company_id="Target company ID"
    )
    async def delete_company(
        self,
        interaction: discord.Interaction,
        company_id: str
    ):
        await interaction.response.defer(ephemeral=True)
        clean_id = company_id.strip().lower()

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM parties WHERE party_id = ? AND is_company = 1",
                (clean_id,)
            )
            company = cursor.fetchone()
            
            if not company:
                await interaction.followup.send(f"❌ Company `{clean_id}` not found.")
                return

            is_admin = interaction.user.guild_permissions.administrator

            # Check permissions based on structure
            if company["structure_type"] == "sole_proprietorship":
                # Check if user is the owner (has all shares)
                cursor.execute(
                    "SELECT shares_owned FROM shares WHERE user_id = ? AND party_id = ?",
                    (interaction.user.id, clean_id)
                )
                shares_row = cursor.fetchone()
                owns_all = shares_row and shares_row["shares_owned"] >= company["total_shares"] * 0.99
                
                if not is_admin and not owns_all:
                    await interaction.followup.send("❌ Only the owner or an admin can delete a sole proprietorship.")
                    return

            elif company["structure_type"] == "partnership":
                # Get all partners
                cursor.execute(
                    "SELECT user_id FROM shares WHERE party_id = ? AND shares_owned > 0",
                    (clean_id,)
                )
                partners = cursor.fetchall()
                
                if not is_admin:
                    if len(partners) > 1:
                        await interaction.followup.send(
                            "❌ Partnership deletion requires all partners to agree. Use admin command or have all partners vote."
                        )
                        return

            # Corporation: admin only
            elif company["structure_type"] == "corporation":
                if not is_admin:
                    await interaction.followup.send("❌ Only admins can delete a corporation.")
                    return

            # Execute deletion
            cursor.execute("DELETE FROM parties WHERE party_id = ?", (clean_id,))
            cursor.execute("DELETE FROM shares WHERE party_id = ?", (clean_id,))
            cursor.execute("DELETE FROM market_orders WHERE party_id = ?", (clean_id,))
            cursor.execute("DELETE FROM stock_history WHERE party_id = ?", (clean_id,))
            cursor.execute("DELETE FROM board_members WHERE company_id = ?", (clean_id,))
            cursor.execute("DELETE FROM company_investments WHERE company_id = ?", (clean_id,))
            cursor.execute("DELETE FROM company_paychecks WHERE company_id = ?", (clean_id,))
            cursor.execute("DELETE FROM company_votes WHERE company_id = ?", (clean_id,))
            cursor.execute("DELETE FROM loan_requests WHERE company_id = ?", (clean_id,))
            conn.commit()

        await interaction.followup.send(f"🗑️ Company **{company['name']}** (`{clean_id}`) has been deleted.")

    @tasks.loop(seconds=60.0)
    async def process_loans(self):
        """Check for overdue loans and mark as defaulted."""
        with get_db() as conn:
            cursor = conn.cursor()
            now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            
            # Find overdue loans
            cursor.execute(
                """
                SELECT l.request_id, l.user_id, l.company_id, l.amount, l.interest_rate, p.name as company_name
                FROM loan_requests l
                JOIN parties p ON l.company_id = p.party_id
                WHERE l.status = 'APPROVED' AND l.due_time <= ?
                """,
                (now,)
            )
            overdue = cursor.fetchall()
            
            for loan in overdue:
                total_owed = loan["amount"] + (loan["amount"] * loan["interest_rate"] / 100)
                
                # Check if user has enough to auto-repay
                cursor.execute("SELECT balance FROM users WHERE user_id = ?", (loan["user_id"],))
                user_row = cursor.fetchone()
                balance = user_row["balance"] if user_row else 0.0
                
                if balance >= total_owed:
                    # Auto-repay
                    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (total_owed, loan["user_id"]))
                    cursor.execute("UPDATE parties SET treasury = treasury + ? WHERE party_id = ?", (total_owed, loan["company_id"]))
                    cursor.execute(
                        "UPDATE loan_requests SET status = 'REPAID' WHERE request_id = ?",
                        (loan["request_id"],)
                    )
                    cursor.execute(
                        "INSERT INTO loan_payments (request_id, user_id, amount) VALUES (?, ?, ?)",
                        (loan["request_id"], loan["user_id"], total_owed)
                    )
                else:
                    # Default the loan
                    cursor.execute(
                        "UPDATE loan_requests SET status = 'DEFAULTED' WHERE request_id = ?",
                        (loan["request_id"],)
                    )
                    # User goes into debt - they can't buy anything until fixed
                    cursor.execute(
                        "UPDATE users SET debt = COALESCE(debt, 0) + ? WHERE user_id = ?",
                        (total_owed, loan["user_id"])
                    )
                    
                    # Notify user
                    try:
                        user = await self.bot.fetch_user(loan["user_id"])
                        await user.send(
                            f"💀 **LOAN DEFAULTED!** Your loan of **${loan['amount']:.2f}** from **{loan['company_name']}** has defaulted.\n"
                            f"You now owe **${total_owed:.2f}** in debt. You cannot make purchases until this is paid off.\n"
                            f"Use `/manage_debt` to see your debt status."
                        )
                    except:
                        pass
                
                conn.commit()

    @app_commands.command(
        name="manage_debt",
        description="💰 View and pay off your debt."
    )
    async def manage_debt(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT debt FROM users WHERE user_id = ?", (interaction.user.id,))
            user_row = cursor.fetchone()
            debt = user_row["debt"] if user_row and user_row["debt"] else 0.0

        embed = discord.Embed(
            title="💰 Your Debt Status",
            color=discord.Color.red() if debt > 0 else discord.Color.green()
        )

        if debt <= 0:
            embed.description = "✅ You have no debt. You're free to make purchases!"
        else:
            embed.description = f"⚠️ You have **${debt:.2f}** in debt."
            embed.add_field(
                name="📋 How to repay",
                value=(
                    "• Debt can be paid by other users sending you money\n"
                    "• You can earn money through message payouts\n"
                    "• Once debt is cleared, you can make purchases again\n\n"
                    f"Use `/pay_debt` to make a payment toward your debt."
                ),
                inline=False
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="pay_debt",
        description="💵 Make a payment toward your debt."
    )
    @app_commands.describe(
        amount="Amount to pay toward debt"
    )
    async def pay_debt(
        self,
        interaction: discord.Interaction,
        amount: float
    ):
        await interaction.response.defer(ephemeral=True)

        if amount <= 0:
            await interaction.followup.send("❌ Amount must be greater than zero.")
            return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT balance, debt FROM users WHERE user_id = ?", (interaction.user.id,))
            user_row = cursor.fetchone()
            
            if not user_row:
                await interaction.followup.send("❌ User not found.")
                return

            balance = user_row["balance"] or 0.0
            debt = user_row["debt"] or 0.0

            if debt <= 0:
                await interaction.followup.send("✅ You have no debt to pay.")
                return

            if balance < amount:
                await interaction.followup.send(f"❌ Insufficient funds! Balance: **${balance:.2f}**.")
                return

            # Pay toward debt
            payment = min(amount, debt)
            cursor.execute(
                "UPDATE users SET balance = balance - ?, debt = debt - ? WHERE user_id = ?",
                (payment, payment, interaction.user.id)
            )
            conn.commit()

        remaining = debt - payment
        if remaining <= 0:
            await interaction.followup.send(f"✅ **Debt Paid Off!** You're free to make purchases again!")
        else:
            await interaction.followup.send(
                f"✅ Paid **${payment:.2f}** toward debt.\n"
                f"Remaining debt: **${remaining:.2f}**"
            )

    @app_commands.command(
        name="manage_company",
        description="⚙️ Company manager suite."
    )
    @app_commands.describe(
        company_id="Target company ID",
        employee="Target employee member (for paycheck)",
        salary="Paycheck salary amount per interval"
    )
    async def manage_company(
        self,
        interaction: discord.Interaction,
        company_id: str,
        employee: discord.Member = None,
        salary: float = None
    ):
        await interaction.response.defer(ephemeral=True)
        clean_id = company_id.strip().lower()

        managed = get_user_managed_parties(interaction.user)
        company = next((p for p in managed if p["party_id"] == clean_id and p["is_company"] == 1), None)

        if not company:
            await interaction.followup.send(f"❌ You do not manage company `{clean_id}`.")
            return

        if salary is not None:
            if salary < 0:
                await interaction.followup.send("❌ Salary cannot be negative.")
                return
            if not employee:
                await interaction.followup.send("❌ Must specify an employee for paycheck.")
                return

            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO company_paychecks (company_id, user_id, salary) VALUES (?, ?, ?)
                    ON CONFLICT(company_id, user_id) DO UPDATE SET salary = ?
                    """,
                    (clean_id, employee.id, salary, salary)
                )
                conn.commit()

            await interaction.followup.send(f"✅ Set salary of **${salary:.2f}** for {employee.mention} in **{company['name']}**.")
        else:
            # Show management options
            embed = discord.Embed(
                title=f"⚙️ Manage {company['name']}",
                description=f"**Type:** {company['structure_type'].replace('_', ' ').title()}\n**Treasury:** ${company['treasury']:.2f}\n**Total Shares:** {company['total_shares']:.2f}",
                color=discord.Color.purple()
            )
            embed.add_field(
                name="📋 Available Actions",
                value=(
                    "• `/vote_ceo` - Vote for CEO\n"
                    "• `/ceo_results` - View election results\n"
                    "• `/request_loan` - Request a loan\n"
                    "• `/approve_loan` - Approve loans (managers)\n"
                    "• `/manage_company <company_id> employee:@{user} salary:<amount>` - Set paycheck\n"
                    "• `/company_info` - View company details\n"
                    "• `/invest` - Invest in the company\n"
                    "• `/delete_company` - Delete (admin/owner only)"
                ),
                inline=False
            )
            await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="paycheck",
        description="💰 Check your current salary from companies."
    )
    async def paycheck(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT c.company_id, c.salary, p.name
                FROM company_paychecks c
                JOIN parties p ON c.company_id = p.party_id
                WHERE c.user_id = ?
                """,
                (interaction.user.id,)
            )
            paychecks = cursor.fetchall()

        if not paychecks:
            await interaction.followup.send("📭 You have no active paychecks.")
            return

        embed = discord.Embed(
            title="💰 Your Paychecks",
            color=discord.Color.green()
        )
        
        total = 0
        for p in paychecks:
            embed.add_field(
                name=f"🏢 {p['name']}",
                value=f"Salary: **${p['salary']:.2f}** per interval",
                inline=False
            )
            total += p['salary']
        
        embed.add_field(
            name="📊 Total",
            value=f"${total:.2f} per interval",
            inline=False
        )
        
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(CompanyCog(bot))