class PayModal(discord.ui.Modal):
    def __init__(self, p, i, g):
        super().__init__(title="購入手続き")
        self.p, self.i, self.g = p, i, g
        self.inp = discord.ui.TextInput(label="PayPay送金リンク", placeholder="https://paypay.ne.jp...", required=True)
        self.add_item(self.inp)
    
    async def callback(self, it: discord.Interaction):
        # 1. タイムアウトを防ぐため、まず即座にDiscordへ応答を返す
        await it.response.defer(ephemeral=True)
        
        oid = str(it.id)
        # 2. 安全にデータを辞書に格納
        orders[oid] = {
            "uid": it.user.id, 
            "g": self.g, 
            "key": self.p["product_key"], 
            "iid": self.i["id"],          # self.i から正しくIDを取得
            "name": self.p["display_name"], 
            "data": self.i["data"],         # self.i から正しくアカウントデータを取得
            "price": self.p["price"], 
            "url": self.inp.value
        }
        
        # 3. 完了メッセージをユーザーに送信
        await it.followup.send("✅ 申請を受け付けました！管理者の承認後、DMにてアカウント情報が届きます。", ephemeral=True)
        
        # 4. 管理者チャンネルへ通知を送信
        await nt(self.p["display_name"], self.p["price"], self.inp.value, it.user.id, oid)
